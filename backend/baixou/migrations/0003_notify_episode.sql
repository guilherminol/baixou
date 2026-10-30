-- Migração 0003: estado de EPISÓDIO de notificação (D-05/D-06).
--
-- Uma linha por aviso operacional em curso. A chave identifica o episódio —
-- "source:<fonte>:failure" e "source:<fonte>:recovery" (falha prolongada e
-- volta ao normal da fonte, ALERT-03) e "quota:warned80:<YYYY-MM>" e
-- "quota:exhausted:<YYYY-MM>" (cota da brapi, mês UTC) — e ``delivered``
-- marca ENTREGA, não a transição (D-06): linha AUSENTE = episódio não
-- iniciado; delivered=0 = pendente, o aviso reenvia toda rodada até o
-- primeiro aceite de canal; delivered=1 = entregue, nunca mais NESTE
-- episódio. As chaves de fonte são APAGADAS/substituídas na transição de
-- recuperação — o episódio fecha e o PRÓXIMO ciclo falha→recuperação da
-- mesma fonte emite os dois avisos DE NOVO (um aviso por EPISÓDIO, não por
-- lifetime da fonte); as de cota self-resetam na virada do mês pela chave.
-- A fase precisava de estado de episódio por fonte/cota que o esquema 0001
-- não tem — a tabela própria evita ALTER em source_state/quota e serve aos
-- quatro episódios (DESVIO documentado do "nenhuma migração nova prevista
-- nesta fase": crescimento aditivo do esquema, registrado na seção de
-- tabelas do arquitetura-v0.1.md). Migrações nunca são editadas depois de
-- publicadas — crescimento só com novos arquivos NNN_*.sql. Datas e horas
-- em UTC ISO-8601 (TEXT).

CREATE TABLE IF NOT EXISTS notify_episode (
    key TEXT NOT NULL PRIMARY KEY,
    delivered INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
