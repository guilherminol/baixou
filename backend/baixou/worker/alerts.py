"""Máquina de estados do alerta: cruzamento, rearme, troca de alvo e entrega (SC-1, SC-2, SC-3).

Precedência por item a cada rodada (arquitetura §Máquina de estados do
alerta; D-12/D-13/D-15/D-16):

- (1) Item alertado com ``alerted_target`` DIFERENTE de ``watch.target_below``
  (incluindo NULL) é TROCA DE ALVO (D-13): write_tx zera alerted=0 e
  alerted_target=NULL mantendo alerted_at, e NADA é enviado nesta rodada —
  a reavaliação é na rodada seguinte (o web não precisa gravar nas cotações).
- (2) Item alertado, mesmo alvo, com preço ≥ alvo × (1 + REARM_PCT ÷ 100) —
  borda INCLUSIVA na margem exata — é REARME (D-12): mesma write_tx de zera
  estado, mantendo alerted_at, e NENHUMA mensagem sai (rearme é mudança de
  estado, não aviso).
- (3) Item alertado abaixo da margem: nada — não repete o aviso (um aviso por
  cruzamento, SC-2).
- (4) Item aguardando (alerted=0) com preço ≤ alvo (borda INCLUSIVA — D-12)
  dispara a entrega: o aviso vai por TODOS os canais configurados, EM
  SEQUÊNCIA e FORA de qualquer transação (disciplina WAL T-02-01 — a rede
  nunca segura o lock).

Regra "o aviso conta como enviado se pelo menos um canal aceitar" fica no
worker (arquitetura §Interfaces de notificadores): com aceite de qualquer
canal (HTTP 2xx — D-16), o item fica alertado — numa write_tx curta PRÓPRIA
grava alerted=1, alerted_at (hora UTC do disparo), alerted_target e
notify_state; com TODOS recusando, a MESMA write_tx grava SOMENTE
notify_state — o item segue aguardando e a rodada seguinte tenta de novo,
alcançando inclusive canais configurados depois do início da tentativa.

notify_state é gravado em TODA tentativa de envio — aceito ou não — como
JSON por canal no formato final consumido pelas fases 4–5 (D-15), com as
chaves na ordem de ``CHANNEL_ORDER`` e valores ``"ok"`` ou
``"erro <kind> (HTTP <n>)"``. NÃO há reenvio seletivo ao canal que falhou:
o próximo cruzamento reenvia por todos (arquitetura).

Item com NENHUM canal configurado não gera envio nem escrita de entrega
(lista vazia — avisos desligados, D-08); rearme e troca de alvo independem de
canal (mudança de estado do item). ``alerted_at`` é histórico — "hora do
último disparo" (ALERT-04) — e SOBREVIVE a rearme e a troca de alvo; após o
rearme, um novo cruzamento para baixo dispara NOVO aviso e grava novo
alerted_at/alerted_target. O GATE de frescor por idade (``freshness_state``:
ok/stale/closed/none — D-11/SC-5) envolve TODA a avaliação: só estado "ok"
avalia qualquer transição (troca de alvo, rearme, cruzamento); sem cotação
("none"), velha ("stale") ou B3 fora do pregão ("closed") nada é enviado nem
escrito.

Avisos operacionais (D-05/D-06/D-07 — ``process_operational``, chamada após
``process_alerts`` na rodada): episódios de FONTE — falha prolongada
(``source_state.consecutive_failures`` ≥ FAIL_NOTIFY_AFTER, valor já gravado
pela write_tx da rodada) e volta ao normal — e de COTA da brapi (80% e 100%
do mês UTC), todos com o MESMO formato do aviso de preço ("Baixou: <evento>",
corpo PT-BR) e a MESMA regra de entrega. O estado do episódio vive na tabela
``notify_episode`` (migração 0003) e a flag marca ENTREGA, não a transição
(D-06): linha AUSENTE = episódio não iniciado; delivered=0 = pendente, o
aviso reenvia toda rodada até o primeiro aceite (alcançando inclusive canais
configurados depois do início do episódio); delivered=1 = entregue, nunca
mais NESTE episódio. A TRANSIÇÃO de recuperação APAGA a chave de falha
(encerrando SEM envio o aviso pendente — reenviar "fonte com falha" depois
da normalização seria informação falsa) e grava por UPSERT a de recovery: o
episódio fecha com o estado zerado, então um SEGUNDO ciclo
falha→recuperação da mesma fonte emite os dois avisos DE NOVO — um aviso por
EPISÓDIO, não por lifetime da fonte (ALERT-03). As chaves de cota carregam o
mês UTC (``quota:warned80:{YYYY-MM}``/``quota:exhausted:{YYYY-MM}``) — a
virada do mês abre episódio naturalmente novo; a linha de quota (month,
calls, warned_80) e a porta de 100% da Fase 2 ficam INTOCADAS — a mensagem é
derivada da linha persistida, quota.py não muda. Avisos operacionais NUNCA
tocam alerted/alerted_at/alerted_target/notify_state de item nenhum (D-07).

Eventos: ``alert_sent`` (uma vez por cruzamento entregue),
``alert_delivery_failed`` por canal recusado, ``alert_rearmed`` e
``alert_target_changed`` (mudanças de estado sem envio),
``source_notify_alert`` e ``quota_notify_alert`` (avisos operacionais) —
citam kind e código HTTP quando falha de canal, nunca URL, token, tópico ou
chat_id (K2/T-03-01/T-03-02). Estilo de worker/quota.py: funções de módulo,
leitura fora de transação, escrita só em write_tx do chamador (aqui: write_tx
própria do resultado do alerta e das flags de episódio).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal

import httpx

from baixou.core.config import Settings
from baixou.core.db import write_tx
from baixou.core.log import log
from baixou.worker import messages, quota
from baixou.worker.notifiers.base import CHANNEL_ORDER, NotificationResult, configured_channels
from baixou.worker.sources.base import b3_market_open

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _rearm_threshold(target: float, rearm_pct: int) -> Decimal:
    """Margem de rearme ``alvo × (1 + REARM_PCT ÷ 100)`` em Decimal exato (D-12).

    A borda é INCLUSIVA na margem exata — e em float a conta não fecha:
    ``15.0 * 1.01`` sai ACIMA do literal ``15.15`` e o rearme nunca dispararia
    na borda (mesma armadilha do ``0.8 * 15000`` da cota de 80%, precedente da
    Fase 2). Decimal sobre ``str`` fecha a conta exata para valores com
    representação decimal finita — o que fontes e seeds produzem.
    """
    return Decimal(str(target)) * (100 + rearm_pct) / 100


def _parse_ts(value: str | None) -> datetime | None:
    """Parse estrito do formato de timestamp do banco; None se ilegível.

    Mesmo padrão de ``worker.main._parse_ts`` — replicado aqui porque ``main``
    importa este módulo (importar de volta fecharia ciclo).
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def freshness_state(row: sqlite3.Row, settings: Settings, now_utc: datetime) -> str:
    """Deriva o estado de frescor da cotação: ``ok`` | ``stale`` | ``closed`` | ``none``.

    Estado DERIVADO por idade + pregão + presença de preço — sem coluna nova
    (nenhuma migração); as Fases 4–5 derivam o MESMO estado para a página
    (D-11). Tabela da arquitetura §Frescor da cotação:

    - ``none``: price ou price_at ausentes (também ilegíveis — sem eles não há
      como aferir idade). A linha nunca avalia, inclusive a que tem
      ``last_fetch_error`` e preço NULL — o que bloqueia é preço ausente, não
      o erro gravado (resolução "empty" de ALERT-04).
    - ``closed``: B3 fora do pregão (``b3_market_open`` False) — mantém a
      última cotação sem reavaliar; tem PRECEDÊNCIA sobre a idade. Cripto
      nunca é closed (mercado 24/7).
    - ``stale``: idade acima do limite por mercado (STALE_MINUTES_B3 75 /
      STALE_MINUTES_CRYPTO 30) — não avalia.
    - ``ok``: o resto — borda INCLUSIVA (idade IGUAL ao limite é ok).

    Consequência D-11: a fonte que falhou na rodada NÃO invalida a cotação
    anterior dentro do limite — ``last_fetch_error`` não entra nesta derivação.
    """
    if row["price"] is None or row["price_at"] is None:
        return "none"
    if row["market"] == "b3" and not b3_market_open(now_utc, settings.tz_market):
        return "closed"
    price_at = _parse_ts(row["price_at"])
    if price_at is None:
        return "none"
    if row["market"] == "b3":
        limit = timedelta(minutes=settings.stale_minutes_b3)
    else:
        limit = timedelta(minutes=settings.stale_minutes_crypto)
    if now_utc - price_at > limit:
        return "stale"
    return "ok"


def _notify_state(results: dict[str, NotificationResult]) -> str:
    """JSON por canal (D-15): {"canal": "ok" | "erro <kind> (HTTP <n>"} na ordem de CHANNEL_ORDER.

    Toda tentativa entra — aceita ou recusada — porque o resultado por canal
    é o que as fases 4–5 consomem (formato congelado, D-15).
    """
    state: dict[str, str] = {}
    for channel in CHANNEL_ORDER:
        result = results.get(channel)
        if result is None:
            continue
        if result.ok:
            state[channel] = "ok"
        elif result.http_status is not None:
            state[channel] = f"erro {result.kind} (HTTP {result.http_status})"
        else:
            state[channel] = f"erro {result.kind}"
    return json.dumps(state, ensure_ascii=False)


def _deliver(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    settings: Settings,
    client: httpx.Client,
    now_utc: datetime,
    channels: list[tuple[str, Callable]],
) -> None:
    """Entrega o aviso de um cruzamento por todos os canais e grava o resultado (SC-3).

    Rede FORA de transação (T-02-01); o resultado — aceito ou não — vai em
    write_tx curta própria. Regra "≥1 canal aceitou": com qualquer aceite o
    item fica alertado; com todos recusando só ``notify_state`` é gravado e a
    rodada seguinte tenta de novo. Sem canal configurado não há envio nem
    escrita (lista vazia — tratado no chamador).
    """
    symbol = row["symbol"]
    title = messages.alert_title(symbol)
    body = messages.price_alert_body(
        symbol,
        row["market"],
        row["price"],
        row["target_below"],
        row["change_pct"],
        row["price_at"],
        settings,
    )
    results: dict[str, NotificationResult] = {}
    for channel, send in channels:
        result = send(client, settings, title, body)  # rede FORA de transação (T-02-01)
        results[channel] = result
        if not result.ok:
            log(
                "warning",
                "alert",
                "alert_delivery_failed",
                channel=channel,
                kind=result.kind,
                http_status=result.http_status,
                text=f"falha de entrega no canal {channel}: {result.kind}",
            )

    accepted = [
        channel
        for channel in CHANNEL_ORDER
        if results.get(channel) is not None and results[channel].ok
    ]
    with write_tx(conn):  # write_tx curta PRÓPRIA do resultado (T-02-01)
        if accepted:
            conn.execute(
                """
                UPDATE quote
                SET alerted = 1, alerted_at = ?, alerted_target = ?, notify_state = ?
                WHERE symbol = ? AND market = ?
                """,
                (
                    now_utc.strftime(_TS_FORMAT),
                    row["target_below"],
                    _notify_state(results),
                    symbol,
                    row["market"],
                ),
            )
        else:
            # Todos recusaram: SOMENTE notify_state — o item segue aguardando
            # (alerted permanece 0) e a rodada seguinte tenta de novo (SC-3).
            conn.execute(
                "UPDATE quote SET notify_state = ? WHERE symbol = ? AND market = ?",
                (_notify_state(results), symbol, row["market"]),
            )
    if accepted:
        log(
            "info",
            "alert",
            "alert_sent",
            symbol=symbol,
            channels=accepted,
            text=f"aviso de preço enviado: {symbol}",
        )


def _reset_alert_state(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    """Volta o item a aguardando mantendo ``alerted_at`` como histórico (ALERT-04).

    Escrita em write_tx curta própria (T-02-01): zera ``alerted`` e
    ``alerted_target``; ``alerted_at`` e ``notify_state`` ficam intactos —
    rearme e troca de alvo são mudança de estado, nunca entrega.
    """
    with write_tx(conn):
        conn.execute(
            "UPDATE quote SET alerted = 0, alerted_target = NULL WHERE symbol = ? AND market = ?",
            (row["symbol"], row["market"]),
        )


def _evaluate_alerted(conn: sqlite3.Connection, row: sqlite3.Row, settings: Settings) -> None:
    """Precedência sobre o item alertado: troca de alvo → rearme → nada (D-13/D-12).

    (1) ``alerted_target`` DIFERENTE de ``watch.target_below`` (incluindo NULL)
    é TROCA DE ALVO: zera o estado e reavalia na RODADA SEGUINTE — nada é
    enviado nesta rodada (o web não precisa gravar nas cotações). (2) Mesmo
    alvo com preço ≥ alvo × (1 + REARM_PCT ÷ 100) — borda INCLUSIVA na margem
    exata, em Decimal (mesma armadilha float do ``0.8 × 15000`` da cota de 80%,
    precedente da Fase 2) — é REARME, sem mensagem. (3) Abaixo da margem: nada
    — um aviso por cruzamento (SC-2). Nenhuma mensagem sai daqui; a comparação
    do alvo é contra as linhas persistidas lidas nesta rodada, nunca contra
    memória entre rodadas (T-03-05).
    """
    if row["alerted_target"] is None or row["alerted_target"] != row["target_below"]:
        _reset_alert_state(conn, row)
        log(
            "info",
            "alert",
            "alert_target_changed",
            symbol=row["symbol"],
            text=f"alvo alterado: {row['symbol']} volta a aguardar (reavalia na rodada seguinte)",
        )
        return
    price = Decimal(str(row["price"]))
    if price >= _rearm_threshold(row["target_below"], settings.rearm_pct):
        _reset_alert_state(conn, row)
        log(
            "info",
            "alert",
            "alert_rearmed",
            symbol=row["symbol"],
            text=f"rearme pela margem: {row['symbol']} volta a aguardar",
        )


def process_alerts(
    conn: sqlite3.Connection, settings: Settings, client: httpx.Client, now_utc: datetime
) -> None:
    """Avalia cada item na precedência troca de alvo → rearme → cruzamento (SC-1/SC-2/SC-3).

    Chamar APÓS a write_tx das cotações da rodada: lê ``watch`` LEFT JOIN
    ``quote`` fora de transação (ordem determinística symbol, market), aplica a
    precedência por item sob o GATE de frescor (``freshness_state`` — só estado
    "ok" avalia qualquer transição: D-11/SC-5) e envia pelos canais fora de
    transação (rede nunca dentro de write_tx — T-02-01), gravando cada
    resultado em write_tx curta própria. Item alertado passa por
    ``_evaluate_alerted`` — troca de alvo e rearme mudam estado sem mensagem,
    com ``alerted_at`` preservado; item aguardando com preço ≤ alvo (borda
    inclusiva) dispara a entrega multi-canal. ``now_utc`` (aware) vira o
    ``alerted_at`` no formato de timestamp do banco.
    """
    rows = conn.execute(
        """
        SELECT w.symbol AS symbol, w.market AS market, w.target_below AS target_below,
               q.price AS price, q.price_at AS price_at, q.change_pct AS change_pct,
               q.alerted AS alerted, q.alerted_target AS alerted_target
        FROM watch w
        LEFT JOIN quote q ON q.symbol = w.symbol AND q.market = w.market
        ORDER BY w.symbol, w.market
        """
    ).fetchall()

    channels = configured_channels(settings)
    for row in rows:
        if freshness_state(row, settings, now_utc) != "ok":
            continue  # none/stale/closed não avaliam NENHUMA transição (D-11/SC-5)
        if row["alerted"]:
            _evaluate_alerted(conn, row, settings)  # troca de alvo → rearme → nada
            continue
        if row["price"] > row["target_below"]:
            continue  # aguardando acima do alvo: não cruza (borda ≤ é inclusiva — D-12)
        if not channels:
            continue  # avisos desligados: nenhum envio nem escrita (D-08)
        _deliver(conn, row, settings, client, now_utc, channels)


# --- Avisos operacionais (D-05/D-06/D-07): episódios de fonte e de cota ---


def _episode(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    """Lê a linha do episódio (leitura fora de transação — disciplina WAL)."""
    return conn.execute("SELECT delivered FROM notify_episode WHERE key = ?", (key,)).fetchone()


def _episode_pending(conn: sqlite3.Connection, key: str) -> bool:
    """Episódio sem entrega (D-06): linha AUSENTE (não iniciado) ou delivered=0."""
    row = _episode(conn, key)
    return row is None or not row["delivered"]


def _upsert_episode(conn: sqlite3.Connection, key: str, delivered: bool, now_utc: datetime) -> None:
    """UPSERT da flag de ENTREGA do episódio — chamar DENTRO de write_tx (T-02-01).

    delivered=1 SÓ com aceite de ≥1 canal (HTTP 2xx — D-16); 0 deixa pendente
    (a rodada seguinte tenta de novo). ``updated_at`` em UTC ISO-8601.
    """
    conn.execute(
        """
        INSERT INTO notify_episode (key, delivered, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET
            delivered = excluded.delivered,
            updated_at = excluded.updated_at
        """,
        (key, 1 if delivered else 0, now_utc.strftime(_TS_FORMAT)),
    )


def _record_episode(conn: sqlite3.Connection, key: str, delivered: bool, now_utc: datetime) -> None:
    """Grava a flag do episódio em write_tx curta própria (T-02-01)."""
    with write_tx(conn):
        _upsert_episode(conn, key, delivered, now_utc)


def _send_all(
    channels: list[tuple[str, Callable]],
    settings: Settings,
    client: httpx.Client,
    title: str,
    body: str,
) -> bool:
    """Envia o aviso por TODOS os canais em sequência — rede FORA de transação (T-02-01).

    Devolve True quando ≥1 canal aceita (HTTP 2xx — D-16); cada recusa emite
    ``alert_delivery_failed`` citando só kind e código HTTP (K2). Sem retry
    dentro da rodada: a repetição é na rodada seguinte (D-06).
    """
    delivered = False
    for channel, send in channels:
        result = send(client, settings, title, body)
        if result.ok:
            delivered = True
        else:
            log(
                "warning",
                "alert",
                "alert_delivery_failed",
                channel=channel,
                kind=result.kind,
                http_status=result.http_status,
                text=f"falha de entrega no canal {channel}: {result.kind}",
            )
    return delivered


def _log_operational(event: str, episode: str, delivered: bool, text: str, **fields) -> None:
    """Evento estável EN + texto PT-BR do aviso operacional — sem URL/token (K2)."""
    log(
        "info" if delivered else "warning",
        "alert",
        event,
        episode=episode,
        delivered=delivered,
        **fields,
        text=text,
    )


def _source_failure(
    conn: sqlite3.Connection,
    settings: Settings,
    client: httpx.Client,
    now_utc: datetime,
    channels: list[tuple[str, Callable]],
    source: str,
    kind: str,
) -> None:
    """Episódio de FALHA prolongada da fonte (D-05a, SC-4).

    ``consecutive_failures`` já foi gravado pela write_tx da rodada — aqui é
    só consumo. O aviso dispara ao alcançar FAIL_NOTIFY_AFTER (borda
    inclusiva) e é ÚNICO dentro do episódio depois do primeiro aceite;
    enquanto pendente (delivered=0), a rodada seguinte tenta de novo (D-06).
    """
    state = conn.execute(
        "SELECT consecutive_failures FROM source_state WHERE source = ?", (source,)
    ).fetchone()
    consecutive = state["consecutive_failures"] if state is not None else 0
    key = f"source:{source}:failure"
    if consecutive < settings.fail_notify_after or not _episode_pending(conn, key):
        return  # abaixo do limiar, ou episódio já entregue: nada a fazer
    title, body = messages.source_failure_message(source, kind, consecutive)
    delivered = _send_all(channels, settings, client, title, body)
    _record_episode(conn, key, delivered, now_utc)
    _log_operational(
        "source_notify_alert",
        "failure",
        delivered,
        f"aviso de fonte com falha: {source} ({consecutive} rodadas seguidas)",
        source=source,
    )


def _source_recovery(
    conn: sqlite3.Connection,
    settings: Settings,
    client: httpx.Client,
    now_utc: datetime,
    channels: list[tuple[str, Callable]],
    source: str,
) -> None:
    """Episódio de RECUPERAÇÃO da fonte (D-05b) — transição ou reenvio pendente.

    TRANSIÇÃO (a chave de falha EXISTE): envia o aviso de volta ao normal e,
    numa write_tx curta própria, APAGA a chave de falha — encerrando SEM
    envio o aviso de falha pendente, se houver (reenviar "fonte com falha"
    depois da normalização seria informação falsa) — e grava por UPSERT a
    chave de recovery com a flag de entrega (substitui a linha do episódio
    anterior). O delete É o reset de estado que abre o PRÓXIMO episódio do
    zero: um segundo ciclo falha→recuperação da mesma fonte emite os dois
    avisos DE NOVO (ALERT-03 — um aviso por EPISÓDIO, não por lifetime da
    fonte). REENVIO PENDENTE (chave de falha ausente, recovery com
    delivered=0): repete a entrega até o primeiro aceite (D-06). Com a chave
    de falha ausente e a de recovery ausente ou delivered=1: episódio
    fechado — nada.
    """
    failure_key = f"source:{source}:failure"
    recovery_key = f"source:{source}:recovery"
    if _episode(conn, failure_key) is not None:  # TRANSIÇÃO: episódio de falha em curso
        title, body = messages.source_recovery_message(source)
        delivered = _send_all(channels, settings, client, title, body)
        with write_tx(conn):
            conn.execute("DELETE FROM notify_episode WHERE key = ?", (failure_key,))
            _upsert_episode(conn, recovery_key, delivered, now_utc)
        _log_operational(
            "source_notify_alert",
            "recovery",
            delivered,
            f"aviso de volta ao normal: {source}",
            source=source,
        )
        return
    row = _episode(conn, recovery_key)
    if row is None or row["delivered"]:
        return  # episódio fechado (nunca iniciado ou já entregue)
    title, body = messages.source_recovery_message(source)  # REENVIO PENDENTE
    delivered = _send_all(channels, settings, client, title, body)
    _record_episode(conn, recovery_key, delivered, now_utc)
    _log_operational(
        "source_notify_alert",
        "recovery",
        delivered,
        f"aviso de volta ao normal: {source}",
        source=source,
    )


def _quota_episodes(
    conn: sqlite3.Connection,
    settings: Settings,
    client: httpx.Client,
    now_utc: datetime,
    channels: list[tuple[str, Callable]],
) -> None:
    """Episódios de COTA da brapi como mensagens de canal (D-05c/d, D-06).

    CONSOME a linha gravada pela Fase 2 (``quota.read_quota`` — quota.py NÃO
    muda; a porta de 100% ``b3_quota_allows`` e os eventos de log
    quota_warning_80/quota_exhausted ficam INTOCADOS, D-06): ``warned_80``=1
    e ``calls`` ≥ brapi_monthly_limit no MÊS UTC corrente são as transições
    já derivadas — a mensagem de canal é derivada da linha persistida,
    independente da transição de log que não se repete naturalmente (a chave
    pendente cobre a repetição de entrega que a transição não cobria). A
    chave carrega o mês — a virada abre episódio naturalmente novo (nenhum
    reset manual).
    """
    row = quota.read_quota(conn)
    if row is None or row["month"] != quota.current_month():
        return  # sem linha, ou linha do mês anterior: a virada zera episódio
    if row["warned_80"]:
        key = f"quota:warned80:{row['month']}"
        if _episode_pending(conn, key):
            title, body = messages.quota_80_message(row["calls"], settings.brapi_monthly_limit)
            delivered = _send_all(channels, settings, client, title, body)
            _record_episode(conn, key, delivered, now_utc)
            _log_operational(
                "quota_notify_alert",
                "warned80",
                delivered,
                f"aviso de 80% da cota da brapi ({row['month']})",
                month=row["month"],
            )
    if row["calls"] >= settings.brapi_monthly_limit:
        key = f"quota:exhausted:{row['month']}"
        if _episode_pending(conn, key):
            title, body = messages.quota_exhausted_message()
            delivered = _send_all(channels, settings, client, title, body)
            _record_episode(conn, key, delivered, now_utc)
            _log_operational(
                "quota_notify_alert",
                "exhausted",
                delivered,
                f"aviso de cota da brapi esgotada ({row['month']})",
                month=row["month"],
            )


def process_operational(
    conn: sqlite3.Connection,
    settings: Settings,
    client: httpx.Client,
    now_utc: datetime,
    source_outcomes: dict[str, str],
) -> None:
    """Avisos operacionais: episódios de fonte (falha/recuperação) e de cota (D-05/D-06).

    Chamar APÓS ``process_alerts``, ainda com o cliente httpx da rodada.
    ``source_outcomes`` é fonte → ``"ok"`` | kind das fontes TENTADAS nesta
    rodada (montado em ``run_round`` a partir de successes/source_errors —
    nada de reler o banco para descobrir o que aconteceu na própria rodada;
    o estado ENTRE rodadas continua 100% no banco). Episódios de fonte em
    ordem determinística, depois os de cota; entrega multi-canal FORA de
    transação com flags de ENTREGA em ``notify_episode`` (migração 0003) em
    write_tx curta própria. NUNCA toca quote de item (D-07) nem a linha de
    quota (Fase 2 intocada).
    """
    channels = configured_channels(settings)
    for source in sorted(source_outcomes):
        outcome = source_outcomes[source]
        if outcome == "ok":
            _source_recovery(conn, settings, client, now_utc, channels, source)
        else:
            _source_failure(conn, settings, client, now_utc, channels, source, outcome)
    _quota_episodes(conn, settings, client, now_utc, channels)
