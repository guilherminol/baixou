"""Fábrica única de conexões SQLite, transação de escrita e cheque de versão.

Toda conexão do projeto nasce aqui: ``autocommit=False`` explícito (o default
LEGACY mudará), ``row_factory=sqlite3.Row``, ``foreign_keys=ON`` e
``synchronous=NORMAL`` (seguro em WAL, certo para HDD). O WAL é ativado uma
única vez pelo runner de migrações, fora de transação — nunca por conexão.
Escritas só dentro de ``write_tx()`` (``BEGIN IMMEDIATE``), transações curtas.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from baixou.core.log import log

WORKER_TIMEOUT_S = 10.0
WEB_TIMEOUT_S = 5.0

# Bug de corrupção em WAL corrigido na 3.51.3; backports seguros conhecidos
# (Debian 12 traz 3.40.x, dentro da faixa do bug — cheque obrigatório na subida).
MIN_SAFE_SQLITE = (3, 51, 3)
SAFE_BACKPORTS = {"3.44.6", "3.50.7"}


def connect(db_path: str | Path, timeout_s: float = WEB_TIMEOUT_S) -> sqlite3.Connection:
    """Abre a única forma de conexão do projeto (worker 10 s, web 5 s de busy_timeout).

    ``isolation_level=None`` (SQLite em autocommit) + transações explícitas
    OURS via ``write_tx()`` — a forma documentada como alternativa em
    ARCHITECTURE.md Anti-Pattern 7. O atributo ``autocommit=False`` (PEP 249,
    recomendado pela STACK para 3.12+) não é usável no CPython 3.14.7: ele abre
    um BEGIN eager que sobrevive a commit()/rollback(), envelopa SELECTs em
    transação persistente (fome de checkpoint em WAL) e quebra pragmas de
    escrita e o runner de migrações via executescript.
    """
    conn = sqlite3.connect(str(db_path), timeout=timeout_s, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def write_tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Transação de escrita com ``BEGIN IMMEDIATE``; ROLLBACK no erro.

    COMMIT/ROLLBACK vão como SQL explícito (com isolation_level=None os
    métodos conn.commit()/rollback() não enxergam o BEGIN nosso).
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def check_sqlite_version(log_fn=log) -> str:
    """Cheque de versão na subida: aviso em uma linha se abaixo da faixa segura."""
    version = sqlite3.sqlite_version
    if version in SAFE_BACKPORTS:
        return version
    if _version_tuple(version) < MIN_SAFE_SQLITE:
        log_fn(
            "warning",
            "db",
            "sqlite_version_warning",
            version=version,
            text=(
                "versão do SQLite abaixo da mínima segura (3.51.3) e fora dos "
                "backports conhecidos — risco documentado de corrupção em modo WAL; "
                "atualize a base do container"
            ),
        )
    return version
