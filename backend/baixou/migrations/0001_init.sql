-- Migração 0001: esquema inicial do Baixou (10 tabelas).
--
-- O modo WAL é ativado pelo runner de migrações FORA da transação
-- (PRAGMA journal_mode não muda dentro de uma transação); é persistente no arquivo.
-- Migrações nunca são editadas depois de publicadas — crescimento só com novos
-- arquivos NNN_*.sql. Datas e horas em UTC ISO-8601 (TEXT).

CREATE TABLE IF NOT EXISTS watch (
    symbol TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('b3', 'crypto')),
    target_below REAL NOT NULL CHECK (target_below > 0),
    direction TEXT NOT NULL DEFAULT 'below' CHECK (direction = 'below'),
    PRIMARY KEY (symbol, market)
);

CREATE TABLE IF NOT EXISTS quote (
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    price REAL,
    price_at TEXT,
    change_pct REAL,
    alerted INTEGER NOT NULL DEFAULT 0,
    alerted_at TEXT,
    alerted_target REAL,
    notify_state TEXT,
    PRIMARY KEY (symbol, market),
    FOREIGN KEY (symbol, market) REFERENCES watch (symbol, market) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS source_state (
    source TEXT PRIMARY KEY,
    last_attempt_at TEXT,
    last_ok_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quota (
    source TEXT PRIMARY KEY,
    month TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    warned_80 INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_run TEXT,
    last_ok TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    worker_id TEXT,
    worker_lock_at TEXT
);

-- Linha única do batimento (id = 1), inserida uma só vez.
INSERT INTO run (id, last_run, last_ok, consecutive_failures, worker_id, worker_lock_at)
SELECT 1, NULL, NULL, 0, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM run WHERE id = 1);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_used_at TEXT,
    remember INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS login_attempt (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    failures INTEGER NOT NULL DEFAULT 0,
    blocked_until TEXT
);

CREATE TABLE IF NOT EXISTS setup (
    token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
