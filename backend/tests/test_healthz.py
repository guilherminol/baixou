"""Tracer e2e da Fase 1: batimento do worker → GET /healthz (OPS-01).

Cobre o caminho único de ponta a ponta sob pytest com banco real em tmp_path:
config → migração 0001 → run.last_run gravado pelo worker → 200/503 com corpo
exatamente vazio. Os casos avulsos de migração e de config são a prova mínima
do tracer; o aprofundamento formal (test_migrations.py, test_config.py) chega
no plano 01-02.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from baixou.core.config import load_settings
from baixou.core.db import WORKER_TIMEOUT_S, connect, write_tx
from baixou.core.migrations import apply_migrations
from baixou.web.main import create_app
from baixou.worker.main import run_round
from fastapi.testclient import TestClient

EXPECTED_TABLES = {
    "watch",
    "quote",
    "source_state",
    "quota",
    "run",
    "users",
    "sessions",
    "login_attempt",
    "setup",
    "schema_migrations",
}


def _client(settings) -> TestClient:
    return TestClient(create_app(settings))


def test_migrated_db_without_heartbeat_returns_503_with_empty_body(settings):
    apply_migrations(settings.db_path)
    with _client(settings) as client:
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.content == b""


def test_worker_heartbeat_then_healthz_returns_200_with_empty_body(settings):
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    run_round(conn, settings)  # rodada real (POLL-01) assina (conn, settings)
    conn.close()
    with _client(settings) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.content == b""


def test_stale_heartbeat_older_than_3x_interval_returns_503_with_empty_body(settings):
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    stale = datetime.now(UTC) - timedelta(minutes=3 * settings.poll_minutes)
    with write_tx(conn):
        conn.execute("UPDATE run SET last_run = ? WHERE id = 1", (stale.isoformat(),))
    conn.close()
    with _client(settings) as client:
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.content == b""


def test_worker_records_last_run_as_utc_iso_8601(db, settings):
    run_round(db, settings)
    row = db.execute("SELECT last_run FROM run WHERE id = 1").fetchone()
    parsed = datetime.fromisoformat(row["last_run"].replace("Z", "+00:00"))
    assert parsed.tzinfo is not None
    age = datetime.now(UTC) - parsed
    assert timedelta(0) <= age < timedelta(minutes=1)


def test_migration_0001_applies_exactly_once(settings):
    apply_migrations(settings.db_path)
    apply_migrations(settings.db_path)  # segunda aplicação no mesmo banco: no-op
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        versions = [row["version"] for row in conn.execute("SELECT version FROM schema_migrations")]
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    assert versions == ["0001", "0002", "0003", "0004"]  # 0002 (D-03), 0003 (D-06) e 0004 (pausa) juntas, uma vez
    assert EXPECTED_TABLES <= tables


def test_invalid_poll_minutes_exits_naming_the_variable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ_MARKET", "America/Sao_Paulo")
    monkeypatch.setenv("POLL_MINUTES", "abc")
    monkeypatch.setenv("LOG_LEVEL", "info")
    with pytest.raises(SystemExit):
        load_settings()
    output = capsys.readouterr().out
    assert "POLL_MINUTES" in output
    assert "abc" not in output  # o valor nunca aparece (K2)


# --- Contrato final do /healthz (plano 01-03): borda exata 2× e corpo vazio ---


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _set_last_run(settings, value: str | None) -> None:
    """Injeta a idade do batimento diretamente no banco de teste."""
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        with write_tx(conn):
            conn.execute("UPDATE run SET last_run = ? WHERE id = 1", (value,))
    finally:
        conn.close()


def test_healthz_heartbeat_one_minute_old_returns_200_with_empty_body(settings):
    apply_migrations(settings.db_path)
    _set_last_run(settings, _iso(datetime.now(UTC) - timedelta(minutes=1)))
    with _client(settings) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.content == b""


def test_healthz_boundary_29m59s_returns_200_and_30m00s_returns_503(settings):
    apply_migrations(settings.db_path)
    limit = timedelta(minutes=2 * settings.poll_minutes)
    _set_last_run(settings, _iso(datetime.now(UTC) - limit + timedelta(seconds=1)))
    with _client(settings) as client:
        assert client.get("/healthz").status_code == 200  # 29m59s ainda é vivo
    _set_last_run(settings, _iso(datetime.now(UTC) - limit))
    with _client(settings) as client:
        response = client.get("/healthz")
    assert response.status_code == 503  # borda inclusiva no estouro (idade >= limite)
    assert response.content == b""


def test_healthz_null_last_run_returns_503_with_empty_body(settings):
    apply_migrations(settings.db_path)
    _set_last_run(settings, None)
    with _client(settings) as client:
        response = client.get("/healthz")
    assert response.status_code == 503
    assert response.content == b""


def test_healthz_unparseable_last_run_returns_503_with_empty_body(settings):
    apply_migrations(settings.db_path)
    _set_last_run(settings, "não-é-uma-data")
    with _client(settings) as client:
        response = client.get("/healthz")
    assert response.status_code == 503  # nunca 500 com traceback para o cliente
    assert response.content == b""


def test_healthz_raw_request_without_cookie_or_session_gets_same_result(settings):
    apply_migrations(settings.db_path)
    _set_last_run(settings, _iso(datetime.now(UTC) - timedelta(minutes=1)))
    with _client(settings) as client:
        response = client.get("/healthz")  # requisição crua, sem cookie/sessão
    assert response.status_code == 200
    assert response.content == b""
    assert "set-cookie" not in {key.lower() for key in response.headers}
