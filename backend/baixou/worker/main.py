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

from baixou.core.config import Settings, load_settings
from baixou.core.db import WORKER_TIMEOUT_S, check_sqlite_version, connect, write_tx
from baixou.core.log import configure, log
from baixou.core.migrations import apply_migrations
from baixou.worker.scheduler import next_slot, wait_until

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


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


def run_round(conn: sqlite3.Connection) -> float:
    """Rodada mínima da Fase 1: grava o batimento (run.last_run, UTC ISO-8601).

    Refresca ``worker_lock_at`` junto — lock fresco é efeito colateral do
    sinal de vida; a posse (``worker_id``) é definida uma única vez por
    ``acquire_instance_lock``. Devolve a duração medida com time.monotonic
    (nunca time.time).
    """
    started = time.monotonic()
    now = _utc_now_iso()
    with write_tx(conn):
        conn.execute(
            "UPDATE run SET last_run = ?, worker_lock_at = ? WHERE id = 1",
            (now, now),
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
    _log_round_completed(run_round(conn))  # ao subir: rodada imediata
    tz = ZoneInfo(settings.tz_market)
    while not stop.is_set():
        target = next_slot(datetime.now(UTC), settings.poll_minutes, tz)
        if wait_until(target, stop):
            _log_round_completed(run_round(conn))


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
