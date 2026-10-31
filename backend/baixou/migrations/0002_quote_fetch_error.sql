-- Migração 0002: erro por cotação em quote (D-03).
--
-- Coluna genérica para qualquer fonte: recebe o kind estável (EN) da última
-- falha de busca do par (symbol, market) — rate_limit, not_found, auth,
-- unavailable ou unexpected — e volta a NULL na primeira busca bem-sucedida
-- seguinte. A remoção do item em watch apaga a linha inteira por ON DELETE
-- CASCADE. Sobrevive a reinícios e deixa o erro por item pronto para a
-- API/UI das fases 4–5 (reversibility: costly, decisão consciente do autor).
-- Migrações nunca são editadas depois de publicadas — crescimento só com
-- novos arquivos NNN_*.sql. Datas e horas em UTC ISO-8601 (TEXT).

ALTER TABLE quote ADD COLUMN last_fetch_error TEXT;
