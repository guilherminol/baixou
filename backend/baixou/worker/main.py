"""Entrypoint do worker: config → versão do SQLite → migrações → rodada de subida → laço.

Na subida roda uma rodada na hora (regra do doc) e só depois entra no ritmo
alinhado ao relógio. SIGTERM/SIGINT apenas sinalizam: a saída é limpa, e a
gravação corrente termina antes (refinamento com lock no plano 01-03).
"""

from __future__ import annotations

import signal
import sqlite3
import threading
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from baixou.core.config import load_settings
from baixou.core.db import WORKER_TIMEOUT_S, check_sqlite_version, connect, write_tx
from baixou.core.log import configure, log
from baixou.core.migrations import apply_migrations
from baixou.worker.scheduler import next_slot, wait_until


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_round(conn: sqlite3.Connection) -> float:
    """Rodada mínima da Fase 1: grava o batimento (run.last_run, UTC ISO-8601).

    Devolve a duração medida com time.monotonic (nunca time.time).
    """
    started = time.monotonic()
    with write_tx(conn):
        conn.execute("UPDATE run SET last_run = ? WHERE id = 1", (_utc_now_iso(),))
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


def main() -> None:
    settings = load_settings()
    configure(settings.log_level)
    check_sqlite_version()
    apply_migrations(settings.db_path)

    stop = threading.Event()

    def _request_stop(signum: int, frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    tz = ZoneInfo(settings.tz_market)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    _log_round_completed(run_round(conn))  # ao subir: rodada imediata
    while not stop.is_set():
        target = next_slot(datetime.now(UTC), settings.poll_minutes, tz)
        if wait_until(target, stop):
            _log_round_completed(run_round(conn))
    conn.close()


if __name__ == "__main__":
    main()
