-- Migração 0004: pausa por item em watch (D-09) e quote SEM FK cascade (D-01).
--
-- A pausa é controle do admin que o worker é obrigado a respeitar: item
-- pausado sai do lote das rodadas ANTES de qualquer consulta à fonte — cota
-- da brapi poupada, cotação congelada e visível, nada avaliado nem notificado
-- (WATCH-03). O esquema 0001 não tem a coluna: o ALTER acrescenta ``paused``
-- INTEGER NOT NULL DEFAULT 0 — todo item existente nasce ATIVO e a pausa é o
-- estado excepcional (o INSERT sem paused continua criando item ativo).
--
-- A reconstrução de quote derruba a FOREIGN KEY ON DELETE CASCADE da 0001:
-- a decisão D-01 é a web apagar SÓ a linha de watch e a cotação órfã — com
-- alerted/alerted_at/alerted_target/notify_state — sobreviver à remoção até
-- o pruning do worker na rodada seguinte (DELETE … WHERE NOT EXISTS, mesmo
-- plano 04-03). D-02 (recriação sempre começa ciclo NOVO) cobre o caso em
-- que a órfã ainda existe: o upsert de reset do web (POST/PUT) sobreõe o
-- estado de alerta. A reconstrução é segura porque NENHUM índice ou view
-- depende de quote: CREATE quote_new com as MESMAS colunas de 0001 +
-- last_fetch_error da 0002, PK (symbol, market) e nenhuma chave estrangeira,
-- cópia linha a linha, DROP da tabela antiga e RENAME. Migrações nunca são
-- editadas depois de publicadas — crescimento só com novos arquivos NNN_*.sql.
-- Datas e horas em UTC ISO-8601 (TEXT).

ALTER TABLE watch ADD COLUMN paused INTEGER NOT NULL DEFAULT 0;

CREATE TABLE quote_new (
    symbol TEXT NOT NULL,
    market TEXT NOT NULL,
    price REAL,
    price_at TEXT,
    change_pct REAL,
    alerted INTEGER NOT NULL DEFAULT 0,
    alerted_at TEXT,
    alerted_target REAL,
    notify_state TEXT,
    last_fetch_error TEXT,
    PRIMARY KEY (symbol, market)
);

INSERT INTO quote_new (symbol, market, price, price_at, change_pct,
    alerted, alerted_at, alerted_target, notify_state, last_fetch_error)
SELECT symbol, market, price, price_at, change_pct,
    alerted, alerted_at, alerted_target, notify_state, last_fetch_error
FROM quote;

DROP TABLE quote;
ALTER TABLE quote_new RENAME TO quote;
