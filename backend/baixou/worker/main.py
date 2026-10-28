"""Entrypoint do worker: config → versão do SQLite → migrações → lock → laço.

Na subida roda uma rodada na hora (regra do doc) e só depois entra no ritmo
alinhado ao relógio. Instância única (D-03): o lock vive nas colunas
``worker_id``/``worker_lock_at`` de ``run`` com TTL de 2× o intervalo — o
mesmo limiar do /healthz; um segundo worker sai com o evento
``single_instance_conflict`` e texto PT-BR. SIGTERM/SIGINT apenas setam o
Event de parada (handler mínimo, nenhuma escrita de banco dentro do handler):
a rodada corrente termina inteira (escrita completa ou rollback — nunca pela
metade), o lock é liberado no ``finally`` e a saída é limpa.
"""

from __future__ import annotations

import os
import signal
import socket
import sqlite3
import threading
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from baixou.core.config import Settings, load_settings
from baixou.core.db import WORKER_TIMEOUT_S, check_sqlite_version, connect, write_tx
from baixou.core.log import configure, log
from baixou.core.migrations import apply_migrations
from baixou.worker import quota
from baixou.worker.scheduler import next_slot, wait_until
from baixou.worker.sources import binance, brapi
from baixou.worker.sources.base import QuoteResult, SourceError, b3_market_open

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Timeout explícito do cliente httpx compartilhado do round (Performance Trap
# do research: um só cliente por rodada, conexão reaproveitada entre chamadas).
_SOURCE_TIMEOUT_S = 10.0

# Kinds de nível de FONTE: qualquer ocorrência falha a fonte na rodada.
# ``not_found`` é falha por ITEM (vai para quote.last_fetch_error) e só
# falha a fonte quando TODAS as chamadas tentadas falharam (SC-3/D-03).
_SOURCE_LEVEL_KINDS = frozenset({"auth", "rate_limit", "unavailable", "unexpected"})

# Fase do divisor de cadência da B3 (D-01): contador interno ao processo — a
# rodada de subida conta (fase 0) e um reinício zera (consequência aceita de
# D-01; a válvula de escape é a própria env, manual). Nunca altera o intervalo
# das rodadas nem o TTL do lock — esses derivam só de settings.poll_minutes.
_cadence_rounds = 0


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime(_TS_FORMAT)


def _source_failure_kind(errors: list[SourceError], success_count: int) -> str | None:
    """Kind a gravar em ``source_state``, ou None se a falha foi só por item.

    A fonte conta como FALHA se alguma chamada falhou com kind de nível de
    fonte (auth, rate_limit, unavailable, unexpected) OU se TODAS as chamadas
    tentadas falharam — mesmo que só ``not_found`` (semântica do plano 02-02).
    """
    for exc in errors:
        if exc.kind in _SOURCE_LEVEL_KINDS:
            return exc.kind
    if errors and success_count == 0:
        return errors[0].kind
    return None


def _parse_ts(value: str | None) -> datetime | None:
    """Parse estrito do formato de timestamp do banco; None se ilegível."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def worker_identity() -> str:
    """Identidade do dono do lock: hostname + pid."""
    return f"{socket.gethostname()}:{os.getpid()}"


def lock_ttl_s(settings: Settings) -> int:
    """TTL do lock = 2 × o intervalo das rodadas (mesmo limiar do /healthz)."""
    return 2 * settings.poll_minutes * 60


def acquire_instance_lock(conn: sqlite3.Connection, worker_id: str, ttl_s: int) -> bool:
    """Reivindica a instância única dentro de ``BEGIN IMMEDIATE``.

    Lê a linha id=1 de run: lock de OUTRO dono com idade < ``ttl_s`` recusa
    (devolve False); caso contrário grava worker_id (hostname+pid) e
    worker_lock_at e devolve True. O perdedor de uma disputa simultânea
    espera no busy_timeout e então vê o lock ocupado. Lock ilegível ou
    ausente é tratado como obsoleto — nunca deadlock para sempre.
    """
    now = _utc_now_iso()
    with write_tx(conn):
        row = conn.execute("SELECT worker_id, worker_lock_at FROM run WHERE id = 1").fetchone()
        holder = row["worker_id"] if row is not None else None
        stamped = row["worker_lock_at"] if row is not None else None
        if holder is not None and holder != worker_id:
            claimed_at = _parse_ts(stamped)
            now_ts = _parse_ts(now)
            if claimed_at is not None and now_ts is not None:
                if (now_ts - claimed_at).total_seconds() < ttl_s:
                    return False  # lock fresco de outro worker: conflito
        conn.execute(
            "UPDATE run SET worker_id = ?, worker_lock_at = ? WHERE id = 1",
            (worker_id, now),
        )
    return True


def release_instance_lock(conn: sqlite3.Connection, worker_id: str) -> None:
    """Libera o lock dentro de ``write_tx`` — só o dono limpa."""
    with write_tx(conn):
        conn.execute(
            "UPDATE run SET worker_id = NULL, worker_lock_at = NULL WHERE id = 1 AND worker_id = ?",
            (worker_id,),
        )


def run_round(conn: sqlite3.Connection, settings: Settings) -> float:
    """Rodada real (POLL-01): watch → fontes FORA de transação → write_tx única.

    (1) lê ``watch`` (leitura fora de transação); (2) consulta as fontes com
    UM ``httpx.Client`` compartilhado do round — brapi um símbolo por chamada
    em sequência (concorrência 1 da brapi; token vem de ``settings.brapi_token``,
    nunca de literal) e Binance em lote único; (3) grava ``quote``,
    ``source_state`` e o batimento (``last_run`` + ``worker_lock_at`` +
    ``last_ok`` quando nenhuma fonte consultada falhou) numa ÚNICA transação
    curta — I/O de rede nunca dentro de transação de escrita (disciplina WAL,
    T-02-01). Falha tipada de uma fonte NUNCA derruba a rodada nem a outra
    fonte (SC-3): a falha fica gravada por fonte em ``source_state``
    (``last_error`` = kind, ``consecutive_failures`` acumula; qualquer sucesso
    zera e limpa) e por item em ``quote.last_fetch_error`` (D-03, migração
    0002). O evento ``source_error`` cita o kind e o símbolo — nunca a URL
    nem o token (K2/T-02-04). Duração via time.monotonic (nunca time.time).
    SC-1/D-01: a B3 só é consultada dentro do pregão (seg–sex 10h–18h no fuso
    TZ_MARKET, via ``b3_market_open``) e quando a fase do divisor
    ``_cadence_rounds % b3_every_n_rounds`` bate; cripto é o dia todo, menos
    os símbolos em quarentena (``last_fetch_error = 'not_found'`` em quote —
    D-02, estado derivado da coluna da 0002, que a cascata limpa ao recriar
    a linha em watch). POLL-03/D-05/D-06/SC-4: cada ``fetch_one`` da brapi
    soma 1 na cota mensal persistida (tabela quota, linha source="brapi") e o
    registro entra na MESMA transação única — virada do mês UTC, aviso único
    de 80% e porta de cota vivem em worker.quota; a porta é consultada ANTES
    de cada fetch_one da B3 e, a 100%, fecha (a última permitida executa e
    conta; bloqueada não sai, não soma e não loga erro — cripto nunca é
    bloqueada por cota da brapi; 429 do 02-02 é falha de fonte, não cota).
    """
    global _cadence_rounds

    started = time.monotonic()
    b3_due = _cadence_rounds % settings.b3_every_n_rounds == 0
    _cadence_rounds += 1  # a rodada de subida conta (fase 0); reinício zera (D-01)
    watch_rows = conn.execute("SELECT symbol, market FROM watch ORDER BY symbol, market").fetchall()
    b3_symbols = [row["symbol"] for row in watch_rows if row["market"] == "b3"]
    crypto_symbols = [row["symbol"] for row in watch_rows if row["market"] == "crypto"]

    quotes: dict[tuple[str, str], QuoteResult] = {}
    item_errors: dict[tuple[str, str], SourceError] = {}
    failures: dict[str, list[SourceError]] = {}
    successes: dict[str, int] = {}
    attempted: list[str] = []
    brapi_calls = 0  # fetch_one executados da brapi nesta rodada (POLL-03)
    with httpx.Client(timeout=_SOURCE_TIMEOUT_S) as client:
        if b3_symbols and b3_due and b3_market_open(datetime.now(UTC), settings.tz_market):
            brapi_errors: list[SourceError] = []
            brapi_ok = 0
            for symbol in b3_symbols:  # serializado: um símbolo por chamada
                if not quota.b3_quota_allows(conn, settings, brapi_calls):
                    break  # 100% (T-02-09): bloqueada não sai, não soma, não loga erro
                brapi_calls += 1  # cada fetch_one executado soma 1 na cota (POLL-03)
                try:
                    quotes[(symbol, "b3")] = brapi.fetch_one(client, symbol, settings.brapi_token)
                    brapi_ok += 1
                except SourceError as exc:
                    brapi_errors.append(exc)
                    item_errors[(symbol, "b3")] = exc
            if brapi_calls:  # porta fechada desde o início: a brapi nem é "tentada" na rodada
                attempted.append("brapi")
                if brapi_errors:
                    failures["brapi"] = brapi_errors
                successes["brapi"] = brapi_ok
        if crypto_symbols:
            # Quarentena derivada da coluna da 0002 (D-02/D-03): símbolo com
            # last_fetch_error = 'not_found' fica FORA das rodadas seguintes
            # — sem chamada 1-a-1 recorrente (peso 4 do lote único preservado).
            # Volta só quando a linha em watch for recriada (cascata limpa a
            # coluna). Vale só para Binance: a brapi falha 1-a-1 naturalmente.
            quarantined = {
                row["symbol"]
                for row in conn.execute(
                    "SELECT symbol FROM quote "
                    "WHERE market = 'crypto' AND last_fetch_error = 'not_found'"
                )
            }
            crypto_symbols = [s for s in crypto_symbols if s not in quarantined]
        if crypto_symbols:  # a quarentena pode esvaziar a lista: nada a consultar
            attempted.append("binance")
            try:
                batch, batch_errors = binance.fetch_batch(client, crypto_symbols)
                for symbol, result in batch.items():
                    quotes[(symbol, "crypto")] = result
                successes["binance"] = len(batch)
                if batch_errors:
                    failures["binance"] = list(batch_errors.values())
                    for symbol, exc in batch_errors.items():
                        item_errors[(symbol, "crypto")] = exc
            except SourceError as exc:
                failures["binance"] = [exc]
                successes["binance"] = 0

    source_errors: dict[str, str] = {}
    for source, errors in failures.items():
        kind = _source_failure_kind(errors, successes.get(source, 0))
        if kind is not None:
            source_errors[source] = kind

    with write_tx(conn):  # transação única e curta: só escrita de banco
        now = _utc_now_iso()
        for (symbol, market), result in quotes.items():
            conn.execute(
                """
                INSERT INTO quote (symbol, market, price, price_at, change_pct, last_fetch_error)
                VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT (symbol, market) DO UPDATE SET
                    price = excluded.price,
                    price_at = excluded.price_at,
                    change_pct = excluded.change_pct,
                    last_fetch_error = NULL
                """,
                (symbol, market, result.price, result.price_at, result.change_pct),
            )
        for (symbol, market), exc in item_errors.items():
            # erro por item (D-03): a linha nasce se ainda não existe (preço
            # NULL) e o último preço conhecido é preservado quando existe
            conn.execute(
                """
                INSERT INTO quote (symbol, market, price, price_at, change_pct, last_fetch_error)
                VALUES (?, ?, NULL, NULL, NULL, ?)
                ON CONFLICT (symbol, market) DO UPDATE SET
                    last_fetch_error = excluded.last_fetch_error
                """,
                (symbol, market, exc.kind),
            )
        # Cota da brapi (POLL-03/D-05/D-06): soma as chamadas efetuadas na
        # MESMA transação única; virada UTC e transições de aviso são do
        # registro (log é stdout, pode sair aqui dentro). Bloqueadas não chegam.
        quota.record_round_calls(conn, settings, brapi_calls)
        for source in attempted:
            if source in source_errors:
                conn.execute(
                    """
                    INSERT INTO source_state
                        (source, last_attempt_at, last_ok_at, consecutive_failures, last_error)
                    VALUES (?, ?, NULL, 1, ?)
                    ON CONFLICT (source) DO UPDATE SET
                        last_attempt_at = excluded.last_attempt_at,
                        consecutive_failures = source_state.consecutive_failures + 1,
                        last_error = excluded.last_error
                    """,
                    (source, now, source_errors[source]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO source_state
                        (source, last_attempt_at, last_ok_at, consecutive_failures, last_error)
                    VALUES (?, ?, ?, 0, NULL)
                    ON CONFLICT (source) DO UPDATE SET
                        last_attempt_at = excluded.last_attempt_at,
                        last_ok_at = excluded.last_ok_at,
                        consecutive_failures = 0,
                        last_error = NULL
                    """,
                    (source, now, now),
                )
        if source_errors:
            # batimento incondicional; last_ok não é tocado quando uma fonte falhou
            conn.execute(
                "UPDATE run SET last_run = ?, worker_lock_at = ? WHERE id = 1",
                (now, now),
            )
        else:
            conn.execute(
                "UPDATE run SET last_run = ?, worker_lock_at = ?, last_ok = ? WHERE id = 1",
                (now, now, now),
            )

    # Eventos de erro FORA da transação: citam kind e símbolo — nunca a URL
    # nem o token (as mensagens das fontes são construídas sem a requisição).
    for (symbol, _market), exc in item_errors.items():
        log(
            "warning",
            "source",
            "source_error",
            kind=exc.kind,
            symbol=symbol,
            text=f"falha ao consultar cotação: {exc.kind}",
        )
    for source, errors in failures.items():
        if source in source_errors and not any(exc.symbol for exc in errors):
            log(
                "warning",
                "source",
                "source_error",
                source=source,
                kind=source_errors[source],
                text=f"falha na fonte {source}: {source_errors[source]}",
            )
    log("info", "worker", "heartbeat")
    return time.monotonic() - started


def _log_round_completed(duration_s: float) -> None:
    log(
        "info",
        "worker",
        "round_completed",
        duration_s=duration_s,
        text="rodada concluída",
    )


def serve_until(conn: sqlite3.Connection, settings: Settings, stop: threading.Event) -> None:
    """Rodada de subida + laço alinhado ao relógio até ``stop`` ser acionado.

    O alvo é sempre recalculado do relógio civil no fuso TZ_MARKET
    (Anti-Pattern 2) e slots perdidos não são recuperados (Anti-Pattern 3).
    """
    _log_round_completed(run_round(conn, settings))  # ao subir: rodada imediata
    tz = ZoneInfo(settings.tz_market)
    while not stop.is_set():
        target = next_slot(datetime.now(UTC), settings.poll_minutes, tz)
        if wait_until(target, stop):
            _log_round_completed(run_round(conn, settings))


def main() -> None:
    settings = load_settings()
    configure(settings.log_level)
    check_sqlite_version()
    apply_migrations(settings.db_path)

    worker_id = worker_identity()
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    if not acquire_instance_lock(conn, worker_id, lock_ttl_s(settings)):
        log(
            "error",
            "worker",
            "single_instance_conflict",
            text="outro worker está ativo; encerrando",
        )
        raise SystemExit(1)

    stop = threading.Event()

    def _request_stop(signum: int, frame: object) -> None:
        stop.set()  # handler mínimo: o laço termina a rodada corrente inteira

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    try:
        serve_until(conn, settings, stop)
    finally:
        release_instance_lock(conn, worker_id)  # saída limpa libera a instância
        conn.close()


if __name__ == "__main__":
    main()
