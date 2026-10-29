"""GET /api/status — estado operacional estruturado para a interface (plano 05-01, D-03).

UM endpoint autenticado alimenta o cabeçalho da página de alertas E a tela
/status inteira (UI-02): última/próxima rodada, cota do mês UTC, estado de
cada fonte e de cada canal. Tudo é derivado NO SERVIDOR na leitura — os
limiares (FAIL_NOTIFY_AFTER, POLL_MINUTES/TZ_MARKET, limites de cota) NUNCA
atravessam o contrato (proibição D-02) e NENHUM segredo de canal aparece no
corpo: canais expõem só {nome, configurado} (K2/T-05-01).

Leitura PURA no molde do GET /api/watch (watch.py): conexão por request
aberta/fechada no finally (molde do /healthz), leituras fora de transação
(disciplina WAL) e ZERO ``write_tx`` no módulo — o estado vem das tabelas que
o worker grava (run/quota/source_state/quote), o web não conserta nem escreve
nada. ``ultima_rodada`` usa o MESMO parse tolerante do /healthz (ilegível →
null, nunca 500); ``proxima_rodada`` é derivada com ``next_slot`` do núcleo
comum (D-04 — fonte ÚNICA do cálculo de slot, molde currency_for), nunca lida
de tabela que pode defasar.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request

from baixou.core.config import Settings
from baixou.core.db import WEB_TIMEOUT_S, connect
from baixou.core.schedule import next_slot
from baixou.web.auth import Sessao
from baixou.web.schemas import StatusCanal, StatusCota, StatusFonte, StatusResponse
from baixou.worker import quota
from baixou.worker.notifiers.base import CHANNEL_ORDER

router = APIRouter()

# Tupla FIXA das fontes do produto (arquitetura: brapi para B3, Binance para cripto) —
# a ordem determinística é a mesma da tela /status (D-14: uma linha por fonte).
_FONTES = ("brapi", "binance")


def _abrir(request: Request) -> sqlite3.Connection:
    """Conexão por request (molde do /healthz), settings vindos do app.state."""
    settings = request.app.state.settings
    return connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)


def _agora() -> datetime:
    """Relógio do módulo — ponto de injeção dos testes (molde _now de auth.py)."""
    return datetime.now(UTC)


def _last_run_legivel(cru: object) -> str | None:
    """run.last_run cru → a string do banco quando legível; ausente/ilegível → None.

    MESMO parse tolerante do /healthz (fromisoformat com replace("Z", "+00:00");
    naive é ilegível como lá): qualquer falha vira null — a leitura nunca derruba.
    """
    if not cru:
        return None
    try:
        parsed = datetime.fromisoformat(str(cru).replace("Z", "+00:00"))
    except TypeError, ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return str(cru)


def _notify_state_parseado(cru: object) -> dict | None:
    """JSON TEXT → objeto; ilegível ou não-objeto → null (molde watch.py — importar
    de lá fecharia ciclo de router em router)."""
    if cru is None:
        return None
    try:
        parsed = json.loads(str(cru))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _ultimo_resultado_por_canal(conn: sqlite3.Connection) -> dict[str, str]:
    """Último resultado por canal derivado do notify_state (D-15) — sem migração.

    Varre as linhas de quote com notify_state e, para cada canal, fica com o
    valor do item alertado POR ÚLTIMO: alerted_at maior vence; empate decide
    rowid (ordem de gravação); alerted_at ausente ordena antes de qualquer
    timestamp. Canal ausente de todos os parses → ausente do dict (None no
    contrato). Formato dos valores: "ok" | "erro <kind> (HTTP <n>)" — os
    MESMOS que o worker grava (worker/alerts.py _notify_state).
    """
    ultimos: dict[str, str] = {}
    chave_mais_recente: dict[str, tuple[str, int]] = {}
    rows = conn.execute(
        "SELECT notify_state, alerted_at, rowid AS rid FROM quote WHERE notify_state IS NOT NULL"
    ).fetchall()
    for row in rows:
        parseado = _notify_state_parseado(row["notify_state"])
        if not parseado:
            continue  # JSON ilegível não derruba a leitura (mesmo contrato tolerante)
        for canal, resultado in parseado.items():
            if not isinstance(resultado, str):
                continue
            chave = (str(row["alerted_at"] or ""), row["rid"])
            if canal not in chave_mais_recente or chave > chave_mais_recente[canal]:
                chave_mais_recente[canal] = chave
                ultimos[canal] = resultado
    return ultimos


def _fontes(conn: sqlite3.Connection, settings: Settings) -> list[StatusFonte]:
    """Estado por fonte derivado de source_state na LEITURA — o limiar fica aqui."""
    fontes: list[StatusFonte] = []
    for nome in _FONTES:
        row = conn.execute(
            "SELECT last_ok_at, last_error, consecutive_failures FROM source_state "
            "WHERE source = ?",
            (nome,),
        ).fetchone()
        falhas = row["consecutive_failures"] if row is not None else 0
        if falhas <= 0:
            estado = "operacional"
        elif falhas < settings.fail_notify_after:
            estado = "erro"
        else:  # n >= FAIL_NOTIFY_AFTER: borda INCLUSIVA, mesmo limiar do aviso (SC-4)
            estado = "falha_prolongada"
        fontes.append(
            StatusFonte(
                nome=nome,
                estado=estado,
                falhas_seguidas=falhas,
                ultimo_sucesso=row["last_ok_at"] if row is not None else None,
                ultimo_erro=row["last_error"] if row is not None else None,
            )
        )
    return fontes


def _canais(conn: sqlite3.Connection, settings: Settings) -> list[StatusCanal]:
    """Canais na ordem fixa de CHANNEL_ORDER: configurado (Settings) + último resultado.

    O import dos módulos de canal é DENTRO da função (molde configured_channels:
    os canais importam o base — importar no topo não fecha ciclo aqui, mas o
    molde mantém os dois módulos carregados só quando a rota roda). Nenhum
    valor de configuração sai daqui: só o booleano de is_configured (K2).
    """
    from baixou.worker.notifiers import ntfy, telegram

    ultimos = _ultimo_resultado_por_canal(conn)
    canais: list[StatusCanal] = []
    for nome in CHANNEL_ORDER:
        configurado = (
            telegram.is_configured(settings) if nome == "telegram" else ntfy.is_configured(settings)
        )
        canais.append(
            StatusCanal(nome=nome, configurado=configurado, ultimo_resultado=ultimos.get(nome))
        )
    return canais


@router.get("/api/status", response_model=StatusResponse)
def status_geral(request: Request, usuario: Sessao) -> StatusResponse:
    """Estado operacional do serviço — exige sessão (del usuario: só autentica).

    ``ultima_rodada``: run.last_run com parse tolerante (None quando ausente ou
    ilegível). ``proxima_rodada``: next_slot derivada AGORA no servidor com
    POLL_MINUTES/TZ_MARKET (D-04). ``cota``: chamadas do MÊS UTC corrente
    (linha de mês anterior → usadas 0 — virada derivada na leitura, molde
    b3_quota_allows) contra o limite de Settings. Fontes e canais derivados
    como acima. ZERO escrita: conexão fechada no finally.
    """
    del usuario
    settings: Settings = request.app.state.settings
    conn = _abrir(request)
    try:
        row = conn.execute("SELECT last_run FROM run WHERE id = 1").fetchone()
        linha_quota = quota.read_quota(conn)
        usadas = (
            linha_quota["calls"]
            if linha_quota is not None and linha_quota["month"] == quota.current_month()
            else 0
        )
        return StatusResponse(
            ultima_rodada=_last_run_legivel(row["last_run"]) if row is not None else None,
            proxima_rodada=next_slot(
                _agora(), settings.poll_minutes, ZoneInfo(settings.tz_market)
            ).isoformat(),
            cota=StatusCota(usadas=usadas, limite=settings.brapi_monthly_limit),
            fontes=_fontes(conn, settings),
            canais=_canais(conn, settings),
        )
    finally:
        conn.close()
