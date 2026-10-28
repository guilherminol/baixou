"""Instância única, parada limpa e agendador do worker (OPS-01, D-03).

Contrato do plano 01-03:

- ``next_slot`` alinhado ao relógio civil no fuso TZ_MARKET; um agora exato no
  slot devolve o PRÓXIMO; virada de dia; slot perdido nunca é recuperado.
- ``wait_until`` dorme em fatias de até 60 s e retorna na hora quando ``stop``
  é acionado durante a espera.
- Lock de instância única em ``run.worker_id``/``run.worker_lock_at`` (D-03):
  conquista em banco recém-migrado, recusa com lock fresco (idade < 2× o
  intervalo), takeover de lock obsoleto, liberação só pelo dono; o batimento
  da rodada refresca o lock (efeito colateral do sinal de vida).
- ``main()`` em conflito sai != 0 com o evento ``single_instance_conflict`` e
  texto PT-BR (subprocesso com banco pré-travado); parada limpa termina a
  rodada de subida e libera o lock.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from baixou.core.config import load_settings
from baixou.core.db import WORKER_TIMEOUT_S, connect, write_tx
from baixou.core.migrations import apply_migrations
from baixou.worker import main as worker_main
from baixou.worker.scheduler import next_slot, wait_until

_TTL_S = 2 * 15 * 60  # 2 × POLL_MINUTES (15) em segundos


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_next_slot_aligns_to_clock_in_market_timezone():
    tz = ZoneInfo("America/Sao_Paulo")
    now = datetime(2026, 10, 27, 14, 59, 59, tzinfo=UTC)  # 11:59:59 em Brasília
    slot = next_slot(now, 15, tz)
    assert slot == datetime(2026, 10, 27, 15, 0, tzinfo=UTC)  # 12:00 em Brasília


def test_next_slot_at_exact_slot_returns_next_never_current():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 10, 27, 15, 0, 0, tzinfo=UTC)
    assert next_slot(now, 15, tz) == datetime(2026, 10, 27, 15, 15, tzinfo=UTC)


def test_next_slot_day_rollover_returns_next_day_midnight():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 10, 27, 23, 50, 0, tzinfo=UTC)
    assert next_slot(now, 15, tz) == datetime(2026, 10, 28, 0, 0, tzinfo=UTC)


def test_next_slot_after_long_round_skips_missed_slot_never_replays():
    tz = ZoneInfo("UTC")
    now = datetime(2026, 10, 27, 15, 7, 0, tzinfo=UTC)  # o slot :00 ficou no passado
    assert next_slot(now, 15, tz) == datetime(2026, 10, 27, 15, 15, tzinfo=UTC)


class _SlicingEvent:
    """``threading.Event`` de teste que registra o orçamento de cada fatia.

    As fatias "dormem" instantaneamente (o contrato testado é o TAMANHO que
    ``wait_until`` pede, não a espera real); após ``stop_after_slices`` fatias
    o evento é acionado, encerrando o laço.
    """

    def __init__(self, stop_after_slices: int) -> None:
        self._event = threading.Event()
        self._stop_after = stop_after_slices
        self.slice_budgets: list[float] = []

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        self.slice_budgets.append(timeout if timeout is not None else 0.0)
        if len(self.slice_budgets) >= self._stop_after:
            self._event.set()
            return True
        return False


def test_wait_until_returns_true_when_target_reached():
    stop = threading.Event()
    target = datetime.now(UTC) + timedelta(seconds=0.2)
    started = time.monotonic()
    assert wait_until(target, stop) is True
    assert time.monotonic() - started < 5


def test_wait_until_sleeps_in_slices_of_at_most_60_seconds():
    event = _SlicingEvent(stop_after_slices=2)
    target = datetime.now(UTC) + timedelta(seconds=130)  # exigiria 3 fatias
    assert wait_until(target, event) is False
    assert event.slice_budgets == [60.0, 60.0]


def test_wait_until_returns_false_immediately_when_stop_set_mid_wait():
    stop = threading.Event()
    target = datetime.now(UTC) + timedelta(seconds=90)
    timer = threading.Timer(0.3, stop.set)
    timer.start()
    started = time.monotonic()
    assert wait_until(target, stop) is False
    elapsed = time.monotonic() - started
    timer.join()
    assert elapsed < 60  # não esperou a fatia inteira


def test_acquire_instance_lock_claims_fresh_database(db):
    assert worker_main.acquire_instance_lock(db, "host-a:111", _TTL_S) is True
    row = db.execute("SELECT worker_id, worker_lock_at FROM run WHERE id = 1").fetchone()
    assert row["worker_id"] == "host-a:111"
    claimed = datetime.strptime(row["worker_lock_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    assert timedelta(0) <= datetime.now(UTC) - claimed < timedelta(minutes=1)


def test_second_acquire_with_fresh_lock_is_refused(db):
    assert worker_main.acquire_instance_lock(db, "host-a:111", _TTL_S) is True
    assert worker_main.acquire_instance_lock(db, "host-b:222", _TTL_S) is False
    row = db.execute("SELECT worker_id FROM run WHERE id = 1").fetchone()
    assert row["worker_id"] == "host-a:111"  # o dono não muda


def test_stale_lock_past_ttl_is_taken_over(db):
    assert worker_main.acquire_instance_lock(db, "host-a:111", _TTL_S) is True
    stale = _iso(datetime.now(UTC) - timedelta(minutes=31))  # > 2 × POLL_MINUTES
    with write_tx(db):
        db.execute("UPDATE run SET worker_lock_at = ? WHERE id = 1", (stale,))
    assert worker_main.acquire_instance_lock(db, "host-b:222", _TTL_S) is True
    row = db.execute("SELECT worker_id FROM run WHERE id = 1").fetchone()
    assert row["worker_id"] == "host-b:222"


def test_release_instance_lock_clears_only_owner_columns(db):
    assert worker_main.acquire_instance_lock(db, "host-a:111", _TTL_S) is True
    worker_main.release_instance_lock(db, "host-b:222")  # não-dono: no-op
    row = db.execute("SELECT worker_id FROM run WHERE id = 1").fetchone()
    assert row["worker_id"] == "host-a:111"
    worker_main.release_instance_lock(db, "host-a:111")  # dono: limpa
    row = db.execute("SELECT worker_id, worker_lock_at FROM run WHERE id = 1").fetchone()
    assert row["worker_id"] is None
    assert row["worker_lock_at"] is None


def test_round_heartbeat_refreshes_lock_timestamp(db, settings):
    assert worker_main.acquire_instance_lock(db, "host-a:111", _TTL_S) is True
    before = db.execute("SELECT worker_lock_at FROM run WHERE id = 1").fetchone()["worker_lock_at"]
    worker_main.run_round(db, settings)  # rodada real (POLL-01) assina (conn, settings)
    row = db.execute("SELECT worker_id, worker_lock_at, last_run FROM run WHERE id = 1").fetchone()
    assert row["worker_id"] == "host-a:111"
    assert row["worker_lock_at"] >= before  # lock fresco é efeito do batimento
    assert row["last_run"] is not None


def test_clean_stop_finishes_startup_round_and_releases_lock(settings, db):
    assert worker_main.acquire_instance_lock(db, "host-a:111", _TTL_S) is True
    stop = threading.Event()
    stop.set()  # como após SIGTERM: uma rodada de subida e sai limpo
    worker_main.serve_until(db, settings, stop)
    worker_main.release_instance_lock(db, "host-a:111")  # o finally do main()
    row = db.execute("SELECT worker_id, worker_lock_at, last_run FROM run WHERE id = 1").fetchone()
    assert row["worker_id"] is None
    assert row["worker_lock_at"] is None
    assert row["last_run"] is not None  # rodada de subida terminou (não pela metade)


def test_main_in_conflict_exits_nonzero_with_single_instance_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    settings = load_settings()
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    with write_tx(conn):
        conn.execute(
            "UPDATE run SET worker_id = 'ghost:999', worker_lock_at = ? WHERE id = 1",
            (_iso(datetime.now(UTC)),),  # lock fresco de outro dono
        )
    conn.close()

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "baixou.worker.main"],
            capture_output=True,
            text=True,
            timeout=30,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "main() em conflito deveria sair imediatamente (single_instance_conflict); "
            "o processo continuou vivo — lock de instância única não implementado"
        )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "single_instance_conflict" in output
    assert "outro worker está ativo" in output
