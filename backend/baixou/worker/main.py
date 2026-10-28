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
from baixou.worker.scheduler import next_slot, wait_until
from baixou.worker.sources import binance, brapi
from baixou.worker.sources.base import QuoteResult, SourceError

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Timeout explícito do cliente httpx compartilhado do round (Performance Trap
# do research: um só cliente por rodada, conexão reaproveitada entre chamadas).
_SOURCE_TIMEOUT_S = 10.0


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime(_TS_FORMAT)


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
    ``last_ok`` quando nenhuma fonte falhou) numa ÚNICA transação curta —
    I/O de rede nunca dentro de transação de escrita (disciplina WAL,
    T-02-01). Falha tipada de uma fonte não derruba a rodada nem a outra
    fonte; a semântica completa de falha (``last_error``,
    ``consecutive_failures``, quarentena) é o plano 02-02 — aqui a fonte que
    falhou só não recebe linha "ok". Duração via time.monotonic (nunca
    time.time). Filtro de pregão da B3 e cadência (D-01) entram na Task 3;
    nesta fatia todos os tickers são consultados.
    """
    started = time.monotonic()
    watch_rows = conn.execute("SELECT symbol, market FROM watch ORDER BY symbol, market").fetchall()
    b3_symbols = [row["symbol"] for row in watch_rows if row["market"] == "b3"]
    crypto_symbols = [row["symbol"] for row in watch_rows if row["market"] == "crypto"]

    quotes: dict[tuple[str, str], QuoteResult] = {}
    source_errors: dict[str, SourceError] = {}
    attempted: list[str] = []
    with httpx.Client(timeout=_SOURCE_TIMEOUT_S) as client:
        if b3_symbols:
            attempted.append("brapi")
            try:
                for symbol in b3_symbols:  # serializado: um símbolo por chamada
                    quotes[(symbol, "b3")] = brapi.fetch_one(client, symbol, settings.brapi_token)
            except SourceError as exc:
                source_errors["brapi"] = exc
        if crypto_symbols:
            attempted.append("binance")
            try:
                batch = binance.fetch_batch(client, crypto_symbols)
                for symbol, result in batch.items():
                    quotes[(symbol, "crypto")] = result
            except SourceError as exc:
                source_errors["binance"] = exc

    with write_tx(conn):  # transação única e curta: só escrita de banco
        now = _utc_now_iso()
        for (symbol, market), result in quotes.items():
            conn.execute(
                """
                INSERT INTO quote (symbol, market, price, price_at, change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (symbol, market) DO UPDATE SET
                    price = excluded.price,
                    price_at = excluded.price_at,
                    change_pct = excluded.change_pct
                """,
                (symbol, market, result.price, result.price_at, result.change_pct),
            )
        for source in attempted:
            if source in source_errors:
                continue  # fonte que falhou não recebe linha "ok" (02-02 grava a falha)
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
