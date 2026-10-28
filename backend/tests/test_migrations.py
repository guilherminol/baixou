"""Disciplina de migrações: fresh, apply-once, refuse-newer e corrida (D-03, Pitfall 9).

Cinco casos do <behavior> do plano 01-02, Task 2, estendidos pelo plano
03-04 para a 0003 (tabela notify_episode — D-05/D-06). A corrida usa duas
threads com conexões separadas (uma por chamada de apply_migrations) e
largada sincronizada por Barrier — o árbitro do lock ``BEGIN IMMEDIATE`` +
busy_timeout: ambas retornam sem exceção, ``schema_migrations`` fica com
exatamente 1 linha por versão e as tabelas íntegras.
"""

from __future__ import annotations

import sqlite3
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
    "notify_episode",
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
    """Banco vazio: tabelas do esquema completo, versões 0001–0003, evento emitido."""
    applied_now = apply_migrations(settings.db_path)

    assert applied_now == ["0001", "0002", "0003"]
    versions, tables = _schema_state(settings.db_path)
    assert versions == ["0001", "0002", "0003"]
    assert EXPECTED_TABLES <= tables
    assert "migration_applied" in capsys.readouterr().out


def test_second_application_is_noop(settings):
    """Segunda chamada no mesmo banco: sem DDL de novo, sem erro, 3 linhas."""
    apply_migrations(settings.db_path)
    applied_now = apply_migrations(settings.db_path)

    assert applied_now == []
    versions, tables = _schema_state(settings.db_path)
    assert versions == ["0001", "0002", "0003"]
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


def test_notify_episode_table_created_with_delivery_flag(settings):
    """A migração 0003 cria notify_episode: chave do episódio + flag de ENTREGA (D-06).

    delivered NOT NULL DEFAULT 0: linha ausente = episódio não iniciado;
    delivered=0 = pendente (reenvia toda rodada); delivered=1 = entregue
    (nunca mais neste episódio).
    """
    apply_migrations(settings.db_path)

    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        columns = {
            row["name"]: (row["type"], row["notnull"], row["dflt_value"], row["pk"])
            for row in conn.execute("PRAGMA table_info(notify_episode)")
        }
    finally:
        conn.close()
    assert set(columns) == {"key", "delivered", "updated_at"}
    assert columns["key"] == ("TEXT", 1, None, 1)  # PRIMARY KEY do episódio
    assert columns["delivered"] == ("INTEGER", 1, "0", 0)  # NOT NULL DEFAULT 0
    assert columns["updated_at"] == ("TEXT", 1, None, 0)  # NOT NULL (UTC ISO-8601)


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
    # Nada foi aplicado além da recusa: as versões continuam 0001–0003 e 9999.
    versions, _ = _schema_state(settings.db_path)
    assert sorted(versions) == ["0001", "0002", "0003", "9999"]


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
    assert versions == ["0001", "0002", "0003"]  # exatamente 1 linha por versão
    assert EXPECTED_TABLES <= tables  # todas as tabelas íntegras


def test_journal_mode_is_wal_after_apply(settings):
    """WAL ativo no banco após apply_migrations (persistente no arquivo)."""
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_watch_direction_column_defaults_to_below_and_refuses_others(settings):
    """ALERT-01: watch.direction nasce 'below' e o CHECK recusa qualquer outro valor.

    O requisito manda o esquema nascer com a coluna direction (v0.1 aceita só
    'below'): o padrão sem env explícito e a recusa do CHECK são comportamento
    do esquema 0001, provados por INSERT real.
    """
    apply_migrations(settings.db_path)

    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        conn.execute(
            "INSERT INTO watch (symbol, market, target_below) VALUES ('PETR4', 'b3', 10.0)"
        )
        row = conn.execute("SELECT direction FROM watch WHERE symbol = 'PETR4'").fetchone()
        assert row["direction"] == "below"  # padrão do esquema v0.1

        with pytest.raises(sqlite3.IntegrityError):  # v0.1 só aceita 'below'
            conn.execute(
                "INSERT INTO watch (symbol, market, target_below, direction) "
                "VALUES ('BTCUSDT', 'crypto', 10.0, 'above')"
            )
    finally:
        conn.close()
