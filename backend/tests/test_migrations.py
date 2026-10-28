"""Disciplina de migrações: fresh, apply-once, refuse-newer e corrida (D-03, Pitfall 9).

Cinco casos do <behavior> do plano 01-02, Task 2. A corrida usa duas threads
com conexões separadas (uma por chamada de apply_migrations) e largada
sincronizada por Barrier — o árbitro do lock ``BEGIN IMMEDIATE`` +
busy_timeout: ambas retornam sem exceção, ``schema_migrations`` fica com
exatamente 1 linha e as 10 tabelas íntegras.
"""

from __future__ import annotations

import threading

import pytest
from baixou.core.db import WORKER_TIMEOUT_S, connect
from baixou.core.migrations import apply_migrations

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


def _schema_state(db_path) -> tuple[list[str], set[str]]:
    """Versões em schema_migrations e nomes de tabelas do banco."""
    conn = connect(db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        versions = [row["version"] for row in conn.execute("SELECT version FROM schema_migrations")]
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    return versions, tables


def test_fresh_database_applies_0001_once_with_event(settings, capsys):
    """Banco vazio: 10 tabelas, exatamente 1 linha de versão 0001, evento emitido."""
    applied_now = apply_migrations(settings.db_path)

    assert applied_now == ["0001", "0002"]
    versions, tables = _schema_state(settings.db_path)
    assert versions == ["0001", "0002"]
    assert EXPECTED_TABLES <= tables
    assert "migration_applied" in capsys.readouterr().out


def test_second_application_is_noop(settings):
    """Segunda chamada no mesmo banco: sem DDL de novo, sem erro, 2 linhas."""
    apply_migrations(settings.db_path)
    applied_now = apply_migrations(settings.db_path)

    assert applied_now == []
    versions, tables = _schema_state(settings.db_path)
    assert versions == ["0001", "0002"]
    assert EXPECTED_TABLES <= tables


def test_quote_gains_last_fetch_error_column(settings):
    """A migração 0002 acrescenta quote.last_fetch_error TEXT (D-03)."""
    apply_migrations(settings.db_path)

    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        columns = {row["name"]: row["type"] for row in conn.execute("PRAGMA table_info(quote)")}
    finally:
        conn.close()
    assert columns.get("last_fetch_error") == "TEXT"


def test_newer_schema_is_refused(settings, capsys):
    """Versão futura no banco (9999) é recusada com newer_schema_refused antes de aplicar."""
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    with conn:
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) "
            "VALUES ('9999', '2020-01-01T00:00:00Z')"
        )
    conn.close()

    with pytest.raises(SystemExit):
        apply_migrations(settings.db_path)
    assert "newer_schema_refused" in capsys.readouterr().out
    # Nada foi aplicado além da recusa: as versões continuam 0001 e 9999.
    versions, _ = _schema_state(settings.db_path)
    assert sorted(versions) == ["0001", "9999"]


def test_concurrent_apply_race_is_exactly_once(settings):
    """Corrida de subida (Pitfall 9): duas conexões simultâneas, sem erro e sem duplicar."""
    barrier = threading.Barrier(2)
    failures: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            apply_migrations(settings.db_path)
        except BaseException as exc:  # registrado; a asserção decide
            failures.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == []  # ambas retornam sem exceção
    versions, tables = _schema_state(settings.db_path)
    assert versions == ["0001", "0002"]  # exatamente 2 linhas, sem duplicar versão
    assert EXPECTED_TABLES <= tables  # todas as 10 tabelas íntegras


def test_journal_mode_is_wal_after_apply(settings):
    """WAL ativo no banco após apply_migrations (persistente no arquivo)."""
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"
