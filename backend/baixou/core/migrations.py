"""Runner de migrações SQL numeradas, transacional e apply-once (D-03).

Aplicado na subida por AMBOS os serviços: a corrida de migração é resolvida
pelo lock do banco (``BEGIN IMMEDIATE`` + busy_timeout). Recusa banco gravado
por versão mais nova do código. Migrações nunca são editadas após publicadas.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from baixou.core.db import WORKER_TIMEOUT_S, connect
from baixou.core.log import log

_MIGRATION_RE = re.compile(r"^(\d+)_.*\.sql$")

# PRAGMA journal_mode NÃO invoca o busy handler: sob a corrida de subida o
# perdedor recebe "database is locked" na hora. Quem migra leva milissegundos,
# então esperar em fatias curtas é ordens de grandeza abaixo do busy_timeout.
_WAL_SET_RETRIES = 250
_WAL_SET_RETRY_PAUSE_S = 0.02


def default_migrations_dir() -> Path:
    """Diretório de migrações relativo ao pacote (funciona no repo e na imagem)."""
    return Path(__file__).resolve().parents[2] / "migrations"


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_wal(conn: sqlite3.Connection) -> None:
    """Ativa WAL uma vez, fora de transação (idempotente e à prova de corrida).

    Já estando em WAL, nem toca no pragma — evita o ``database is locked``
    imediato que o journal_mode devolve sob a corrida de subida (ele não usa
    o busy handler). Se precisar mudar, o perdedor espera em fatias curtas.
    """
    row = conn.execute("PRAGMA journal_mode").fetchone()
    if row is not None and str(row[0]).lower() == "wal":
        return
    for attempt in range(_WAL_SET_RETRIES):
        try:
            conn.execute("PRAGMA journal_mode = WAL").fetchone()
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == _WAL_SET_RETRIES - 1:
                raise
            time.sleep(_WAL_SET_RETRY_PAUSE_S)


def _discover(migrations_dir: Path) -> list[tuple[str, Path]]:
    found = [
        (m.group(1), p)
        for p in sorted(migrations_dir.glob("*.sql"))
        if (m := _MIGRATION_RE.match(p.name))
    ]
    return found


def apply_migrations(db_path: str | Path, migrations_dir: Path | None = None) -> list[str]:
    """Aplica as migrações pendentes; devolve a lista aplicada nesta chamada."""
    migrations_dir = migrations_dir if migrations_dir is not None else default_migrations_dir()
    migrations = _discover(migrations_dir)
    known_versions = {version for version, _ in migrations}

    conn = connect(db_path, timeout_s=WORKER_TIMEOUT_S)
    applied_now: list[str] = []
    try:
        # Fora de transação (conexão em autocommit): journal_mode é persistente
        # no arquivo e não muda dentro de uma transação.
        _ensure_wal(conn)
        # O runner garante a tabela de controle antes de 0001 (IF NOT EXISTS:
        # mesmo esquema criado pela própria migração). DDL executa em
        # autocommit — commitado imediatamente.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }

        unknown = applied - known_versions
        if unknown:
            log(
                "error",
                "db",
                "newer_schema_refused",
                versions=",".join(sorted(unknown)),
                text=(
                    "o banco foi gravado por uma versão mais recente do projeto; "
                    "abrir com esta versão é recusado"
                ),
            )
            raise SystemExit("banco mais novo que o código — migrações recusadas")

        for version, path in migrations:
            if version in applied:
                continue
            # O INSERT ... SELECT ... WHERE NOT EXISTS é a reivindicação atômica
            # da versão: dentro do lock de BEGIN IMMEDIATE, só UMA conexão grava
            # a linha. O perdedor da corrida espera no busy_timeout, entra no
            # lock depois do COMMIT do vencedor, insere 0 linhas (no-op) e sai
            # limpo — nunca DDL aplicada sem versão, nunca versão duplicada.
            script = (
                "BEGIN IMMEDIATE;\n"
                + path.read_text(encoding="utf-8")
                + "\nINSERT INTO schema_migrations (version, applied_at) "
                + f"SELECT '{version}', '{_utc_now_iso()}' "
                + "WHERE NOT EXISTS (SELECT 1 FROM schema_migrations "
                + f"WHERE version = '{version}');\n"
                + "COMMIT;\n"
            )
            try:
                # Transacional: o próprio script faz BEGIN IMMEDIATE ... COMMIT
                # (a conexão está em isolation_level=None, sem controle
                # implícito do Python). O ROLLBACK abaixo é de resgate — nunca
                # DDL pela metade.
                changes_before = conn.total_changes
                conn.executescript(script)
            except sqlite3.Error:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass  # falhou antes do BEGIN — nada a desfazer
                raise
            if conn.total_changes > changes_before:
                applied_now.append(version)
                log(
                    "info",
                    "db",
                    "migration_applied",
                    version=version,
                    text=f"migração {version} aplicada",
                )
    finally:
        conn.close()
    return applied_now
