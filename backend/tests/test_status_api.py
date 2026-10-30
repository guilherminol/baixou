"""GET /api/status (plano 05-01, D-03): estado operacional derivado no servidor.

Sem rede e com banco real em tmp_path (molde test_watch_api.py): admin criado
e logado pelas PRÓPRIAS rotas de auth, seed direto no banco (run/quota/
source_state/quote fora do client — WAL). O envelope é contrato CONGELADO
(insumo dos tipos TS da Fase 5) — a forma completa sai por IGUALDADE EXATA de
dicionário (precedente 03-05: igualdade chave-a-chave deixa sobra em
silêncio) e os cenários de estado por campo. Nenhum limiar
(FAIL_NOTIFY_AFTER, POLL_MINUTES, STALE_*) e nenhum segredo atravessam o
corpo: os testes provam a ausência dos valores de TELEGRAM_TOKEN/NTFY_TOPIC
(K2/T-05-01) e a borda INCLUSIVA de falha_prolongada.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from baixou.core.db import WEB_TIMEOUT_S, connect, write_tx
from baixou.core.migrations import apply_migrations
from baixou.core.schedule import next_slot
from baixou.web.auth import ensure_setup_token
from baixou.web.main import create_app
from fastapi.testclient import TestClient

SENHA = "senha-super-segura-123"

# Quarta 12:07:33 UTC = 09:07 em Brasília: instante FORA de slot (o next_slot
# derivado no servidor tem de cair em 12:15, o próximo estritamente futuro).
_QUARTA_12H07 = datetime(2026, 11, 4, 12, 7, 33, tzinfo=UTC)


def _token_do_log(saida: str) -> str:
    for line in saida.splitlines():
        if "setup_token_created" in line:
            resto = line.split("token de primeiro acesso: ", 1)[1]
            return resto.split(" ", 1)[0].strip()
    raise AssertionError("evento setup_token_created ausente do log")


def _admin_logado(settings, capsys) -> TestClient:
    """Admin criado e logado pelas PRÓPRIAS rotas de auth (molde do tracer 04-01)."""
    apply_migrations(settings.db_path)
    ensure_setup_token(settings)
    token = _token_do_log(capsys.readouterr().out)
    client = TestClient(create_app(settings))
    assert (
        client.post(
            "/api/setup", json={"token": token, "username": "admin", "password": SENHA}
        ).status_code
        == 201
    )
    assert (
        client.post("/api/auth/login", json={"username": "admin", "password": SENHA}).status_code
        == 200
    )
    return client


def _conn(settings) -> sqlite3.Connection:
    """Conexão de inspeção/semeadura fora do client (WAL: leitura concorrente é segura)."""
    return connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)


def _injeta_relogio_status(monkeypatch, now_utc: datetime) -> None:
    """Congela o relógio da derivação (D-03) em ``baixou.web.status._agora``."""
    from baixou.web import status

    monkeypatch.setattr(status, "_agora", lambda: now_utc)


def _mes_corrente() -> str:
    """Mês UTC corrente no formato "YYYY-MM" (mesma derivação de quota.current_month)."""
    return datetime.now(UTC).strftime("%Y-%m")


def _mes_anterior() -> str:
    """Mês UTC do mês anterior — molde da virada consciente de b3_quota_allows."""
    primeiro_do_mes = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (primeiro_do_mes - timedelta(days=1)).strftime("%Y-%m")


def _seed_quota(conn: sqlite3.Connection, month: str, calls: int) -> None:
    conn.execute(
        "INSERT INTO quota (source, month, calls, warned_80) VALUES ('brapi', ?, ?, 0)",
        (month, calls),
    )


def _seed_source_state(
    conn: sqlite3.Connection,
    source: str,
    *,
    last_ok_at: str | None = None,
    last_error: str | None = None,
    consecutive_failures: int = 0,
) -> None:
    conn.execute(
        "INSERT INTO source_state (source, last_ok_at, last_error, consecutive_failures) "
        "VALUES (?, ?, ?, ?)",
        (source, last_ok_at, last_error, consecutive_failures),
    )


def _seed_watch(conn: sqlite3.Connection, symbol: str, market: str) -> None:
    conn.execute(
        "INSERT INTO watch (symbol, market, target_below) VALUES (?, ?, 10.0)", (symbol, market)
    )


def _seed_quote(
    conn: sqlite3.Connection,
    symbol: str,
    market: str,
    *,
    alerted_at: str | None = None,
    notify_state: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO quote (symbol, market, alerted_at, notify_state) VALUES (?, ?, ?, ?)",
        (symbol, market, alerted_at, notify_state),
    )


# --- Cenário 1: banco recém-migrado sem rodadas — envelope completo (D-03)


def test_status_banco_novo_envelope_completo(settings, capsys, monkeypatch):
    """Sem rodadas: envelope D-03 inteiro por IGUALDADE EXATA (contrato congelado)."""
    client = _admin_logado(settings, capsys)
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    esperado_proxima = next_slot(
        _QUARTA_12H07, settings.poll_minutes, ZoneInfo(settings.tz_market)
    ).isoformat()
    resposta = client.get("/api/status")
    assert resposta.status_code == 200
    assert resposta.json() == {
        "ultima_rodada": None,
        "proxima_rodada": esperado_proxima,
        "cota": {"usadas": 0, "limite": 15000},
        "fontes": [
            {
                "nome": "brapi",
                "estado": "operacional",
                "falhas_seguidas": 0,
                "ultimo_sucesso": None,
                "ultimo_erro": None,
            },
            {
                "nome": "binance",
                "estado": "operacional",
                "falhas_seguidas": 0,
                "ultimo_sucesso": None,
                "ultimo_erro": None,
            },
        ],
        "canais": [
            {"nome": "telegram", "configurado": False, "ultimo_resultado": None},
            {"nome": "ntfy", "configurado": False, "ultimo_resultado": None},
        ],
    }


def test_status_proxima_rodada_e_estritamente_futura(settings, capsys):
    """Sem relógio injetado: proxima_rodada é ISO UTC estritamente futuro (slot único)."""
    client = _admin_logado(settings, capsys)
    corpo = client.get("/api/status").json()
    proxima = datetime.fromisoformat(corpo["proxima_rodada"])
    assert proxima.tzinfo is not None
    assert proxima > datetime.now(UTC)
    assert proxima.minute % settings.poll_minutes == 0  # alinhada ao slot
    assert proxima.second == 0 and proxima.microsecond == 0


# --- Cenário 2: run.last_run — string do banco ou null tolerante


def test_status_ultima_rodada_devolve_a_string_do_banco(settings, capsys, monkeypatch):
    """last_run gravado → a MESMA string ISO sai no corpo (o web não reformata)."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        with write_tx(conn):
            conn.execute("UPDATE run SET last_run = '2026-10-29T12:00:00Z' WHERE id = 1")
    finally:
        conn.close()
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    corpo = client.get("/api/status").json()
    assert corpo["ultima_rodada"] == "2026-10-29T12:00:00Z"


def test_status_ultima_rodada_ilegivel_vira_null_sem_500(settings, capsys, monkeypatch):
    """Parse tolerante (molde /healthz): valor ilegível → null, a leitura NÃO derruba."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        with write_tx(conn):
            conn.execute("UPDATE run SET last_run = 'não-é-data' WHERE id = 1")
    finally:
        conn.close()
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    resposta = client.get("/api/status")
    assert resposta.status_code == 200
    assert resposta.json()["ultima_rodada"] is None


# --- Cenário 3: cota do mês UTC corrente (virada de mês derivada na leitura)


def test_status_cota_do_mes_corrente(settings, capsys, monkeypatch):
    """Linha de quota do mês UTC corrente com calls 3120 → usadas 3120."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_quota(conn, _mes_corrente(), 3120)
    finally:
        conn.close()
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    assert client.get("/api/status").json()["cota"] == {"usadas": 3120, "limite": 15000}


def test_status_cota_de_mes_anterior_usadas_zero(settings, capsys, monkeypatch):
    """Virada de mês (molde b3_quota_allows): linha do mês anterior → usadas 0."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_quota(conn, _mes_anterior(), 3120)
    finally:
        conn.close()
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    assert client.get("/api/status").json()["cota"] == {"usadas": 0, "limite": 15000}


# --- Cenário 4: estado por fonte — operacional/erro/falha_prolongada (borda)


def test_status_fonte_erro_abaixo_do_limiar(settings, capsys, monkeypatch):
    """consecutive_failures 2 (< FAIL_NOTIFY_AFTER) → "erro", com sucesso/erro do banco."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_source_state(
            conn,
            "brapi",
            last_ok_at="2026-10-29T11:00:00Z",
            last_error="rate_limit",
            consecutive_failures=2,
        )
    finally:
        conn.close()
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    corpo = client.get("/api/status").json()
    assert corpo["fontes"][0] == {
        "nome": "brapi",
        "estado": "erro",
        "falhas_seguidas": 2,
        "ultimo_sucesso": "2026-10-29T11:00:00Z",
        "ultimo_erro": "rate_limit",
    }


def test_status_fonte_falha_prolongada_na_borda_inclusiva(settings, capsys, monkeypatch):
    """consecutive_failures == FAIL_NOTIFY_AFTER → "falha_prolongada" (borda INCLUSIVA)."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_source_state(
            conn,
            "binance",
            last_error="unavailable",
            consecutive_failures=settings.fail_notify_after,
        )
    finally:
        conn.close()
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    fonte_binance = client.get("/api/status").json()["fontes"][1]
    assert fonte_binance["nome"] == "binance"
    assert fonte_binance["estado"] == "falha_prolongada"
    assert fonte_binance["falhas_seguidas"] == settings.fail_notify_after
    assert fonte_binance["ultimo_sucesso"] is None
    assert fonte_binance["ultimo_erro"] == "unavailable"


# --- Cenário 5: ultimo_resultado por canal derivado do notify_state (D-15)


def test_status_ultimo_resultado_por_canal_vem_do_notify_state_mais_recente(
    settings, capsys, monkeypatch
):
    """O item alertado POR ÚLTIMO (alerted_at maior) manda o resultado de cada canal."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3")
        _seed_watch(conn, "BTCUSDT", "crypto")
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            alerted_at="2026-10-29T12:00:00Z",  # MAIS recente: este manda
            notify_state='{"telegram": "ok", "ntfy": "erro unavailable"}',
        )
        _seed_quote(
            conn,
            "BTCUSDT",
            "crypto",
            alerted_at="2026-10-28T10:00:00Z",
            notify_state='{"telegram": "erro unexpected (HTTP 400)", "ntfy": "ok"}',
        )
    finally:
        conn.close()
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    canais = {c["nome"]: c for c in client.get("/api/status").json()["canais"]}
    assert canais["telegram"]["ultimo_resultado"] == "ok"
    assert canais["ntfy"]["ultimo_resultado"] == "erro unavailable"


def test_status_sem_nenhum_notify_state_ultimo_resultado_null(settings, capsys, monkeypatch):
    """Sem linha com notify_state em quote nenhum → ultimo_resultado null nos canais."""
    client = _admin_logado(settings, capsys)
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    canais = client.get("/api/status").json()["canais"]
    assert all(c["ultimo_resultado"] is None for c in canais)


# --- Cenário 6: guard de sessão e ausência de segredos (K2/T-05-01)


def test_status_sem_sessao_responde_401(settings, capsys):
    """T-05-02: 401 ANTES de qualquer leitura — o mesmo guard das rotas /api."""
    _admin_logado(settings, capsys)
    with TestClient(create_app(settings)) as anonimo:
        negado = anonimo.get("/api/status")
    assert negado.status_code == 401


def test_status_nao_expoe_segredos_dos_canais(settings, capsys, monkeypatch):
    """K2: canais configurados aparecem só como "configurado": true — NUNCA o valor."""
    com_canais = replace(
        settings,
        telegram_token="segredo-token-nao-vazar",
        telegram_chat_id="123456789",
        ntfy_topic="topico-secreto-nao-vazar",
    )
    client = _admin_logado(com_canais, capsys)
    _injeta_relogio_status(monkeypatch, _QUARTA_12H07)
    resposta = client.get("/api/status")
    assert resposta.status_code == 200
    canais = {c["nome"]: c for c in resposta.json()["canais"]}
    assert canais["telegram"]["configurado"] is True
    assert canais["ntfy"]["configurado"] is True
    assert "segredo-token-nao-vazar" not in resposta.text
    assert "topico-secreto-nao-vazar" not in resposta.text
    assert "123456789" not in resposta.text  # chat_id também é dado do canal
