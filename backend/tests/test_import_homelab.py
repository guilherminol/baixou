"""Testes do comando de importação da lista do homelab (OPS-03, FR-17 — plano 06-03).

O import traz tickers e alvos do banco antigo do homelab para a tabela
``watch`` do Baixou SEM o estado "alertado": as linhas nascem com
``direction='below'`` e ``paused=0`` (defaults do esquema — o INSERT nunca
explicita essas colunas) e a primeira rodada recalcula os estados. Só roda
em banco vazio (D-02) e é all-or-nothing: leitura/validação do homelab fora
de transação, gravação em UMA ``write_tx`` — qualquer recusa sai com
``SystemExit(1)`` e COUNT de watch inalterado. K2 (T-06-05): nada do banco
antigo além de símbolo, alvo e mercado aparece na saída — o fixture injeta
uma coluna extra com valor-fake parecido com token e prova a ausência.
Zero rede: dois bancos reais em ``tmp_path`` (homelab sintético + alvo),
molde ``test_reset_password.py`` (argv sob monkeypatch, SystemExit, capsys,
asserção whole-table).
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest
from baixou.core.db import WORKER_TIMEOUT_S, connect, write_tx
from baixou.core.migrations import apply_migrations

# DDL do homelab sintético — TRANSCRITO do CONTRATO documentado em
# .planning/phases/06-empacotamento-release-e-paridade-com-o-homelab/homelab-schema.sql
# (autorado do zero por decisão do usuário no checkpoint de 30/10/2026 — o
# banco do homelab está vazio; este é o esquema REAL ratificado, nunca um
# inventado). A coluna extra do teste K2 entra por ALTER no fixture dedicado,
# fora deste contrato.
_DDL_HOMELAB = """
CREATE TABLE IF NOT EXISTS watch (
    symbol        TEXT    NOT NULL,
    market        TEXT    NOT NULL DEFAULT 'b3' CHECK (market IN ('b3', 'crypto')),
    target        REAL    NOT NULL CHECK (target > 0),
    alerted       INTEGER NOT NULL DEFAULT 0,
    atualizado_em TEXT,
    PRIMARY KEY (symbol, market)
);
"""

# DDL variante para a recusa de coluna ausente: a tabela existe, a coluna de
# alvo do mapeamento não (a mensagem do comando precisa nomeá-la).
_DDL_SEM_COLUNA_ALVO = """
CREATE TABLE IF NOT EXISTS watch (
    symbol TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'b3',
    PRIMARY KEY (symbol, market)
);
"""

# DDL variante para a recusa de par duplicado, SEM a PRIMARY KEY do contrato:
# o módulo valida duplicidade na LEITURA justamente porque um homelab real
# pode não ter a PK — o fixture tira a restrição para construir o caso.
_DDL_SEM_PK = """
CREATE TABLE IF NOT EXISTS watch (
    symbol        TEXT    NOT NULL,
    market        TEXT    NOT NULL DEFAULT 'b3',
    target        REAL    NOT NULL,
    alerted       INTEGER NOT NULL DEFAULT 0,
    atualizado_em TEXT
);
"""

# 2 itens B3 + 2 cripto, com o estado "alertado" do homelab GRUDADO nas
# linhas (alerted=1) — o import nunca o leva: as linhas do alvo nascem
# direction='below'/paused=0 (fr-17).
_ITENS_PADRAO = [
    ("PETR4", "b3", 32.5, 1),
    ("VALE3", "b3", 55.0, 1),
    ("BTCUSDT", "crypto", 50000.0, 1),
    ("ETHUSDT", "crypto", 2500.0, 0),
]

# Valor-fake parecido com token (K2/T-06-05) + nome da coluna extra fora do
# mapeamento: NENHUM dos dois pode aparecer na saída do comando.
_COLUNA_EXTRA = "token_legado"
_TOKEN_FALSO = "ghp_FAKE-1234567890abcdef-token-legado-do-homelab"


def _modulo():
    """Importa o módulo DENTRO do corpo do teste (precedente 06-04): no RED
    o ImportError cai no corpo e a suíte falha teste a teste — nunca em
    coleta, que invalidaria a evidência RED."""
    from baixou.worker import import_homelab

    return import_homelab


def _cria_homelab(
    tmp_path: Path,
    insercoes: list[tuple] = _ITENS_PADRAO,
    ddl: str = _DDL_HOMELAB,
    coluna_extra: str | None = None,
) -> Path:
    """Homelab sintético com o DDL TRANSCRITO do contrato (homelab-schema.sql).

    ``coluna_extra`` acrescenta uma coluna FORA do mapeamento e recebe o
    valor-fake parecido com token em toda linha (só no teste K2).
    """
    caminho = tmp_path / "stock.db"
    conn = sqlite3.connect(str(caminho))
    try:
        conn.executescript(ddl)
        if insercoes and coluna_extra is None:
            conn.executemany(
                "INSERT INTO watch (symbol, market, target, alerted) VALUES (?, ?, ?, ?)",
                insercoes,
            )
        elif insercoes:  # coluna extra FORA do mapeamento (teste K2)
            conn.execute(f"ALTER TABLE watch ADD COLUMN {coluna_extra} TEXT")
            conn.executemany(
                f"INSERT INTO watch (symbol, market, target, alerted, {coluna_extra}) "
                "VALUES (?, ?, ?, ?, ?)",
                [(*linha, _TOKEN_FALSO) for linha in insercoes],
            )
        conn.commit()
    finally:
        conn.close()
    return caminho


def _preenche_watch(settings, symbol: str = "ITSA4") -> None:
    """Uma watch pré-existente no banco-alvo (caso "não vazio" do D-02)."""
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        with write_tx(conn):
            conn.execute(
                "INSERT INTO watch (symbol, market, target_below) VALUES (?, ?, ?)",
                (symbol, "b3", 10.0),
            )
    finally:
        conn.close()


def _roda_import(monkeypatch, caminho_homelab: Path) -> None:
    """Roda main() com o argv sob controle (molde _roda_reset).

    O caminho do homelab é o ÚNICO argumento — nada de segredo por flag (K2).
    """
    monkeypatch.setattr(sys, "argv", ["baixou.worker.import_homelab", str(caminho_homelab)])
    _modulo().main()


def _contagem(settings, tabela: str) -> int:
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {tabela}").fetchone()["n"]
    finally:
        conn.close()


def _linhas_watch(settings) -> list:
    """Whole-table da watch do alvo: símbolo, mercado, alvo e os DEFAULTS."""
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        return conn.execute(
            "SELECT symbol, market, target_below, direction, paused "
            "FROM watch ORDER BY symbol, market"
        ).fetchall()
    finally:
        conn.close()


def test_importacao_bem_sucedida_grava_4_itens_com_defaults_e_relata_moedas(
    settings, monkeypatch, tmp_path, capsys
):
    """Homelab com 2 B3 + 2 cripto → 4 linhas em watch, direction/paused
    nascem dos DEFAULTS ('below'/0 — nunca valor do homelab de estado) e o
    relatório imprime total, itens por mercado com a moeda (PETR4 → R$,
    BTCUSDT → US$) e o aviso da primeira rodada (D-04)."""
    caminho = _cria_homelab(tmp_path)
    _roda_import(monkeypatch, caminho)

    linhas = _linhas_watch(settings)
    assert len(linhas) == 4
    assert {(linha["symbol"], linha["market"], linha["target_below"]) for linha in linhas} == {
        ("PETR4", "b3", 32.5),
        ("VALE3", "b3", 55.0),
        ("BTCUSDT", "crypto", 50000.0),
        ("ETHUSDT", "crypto", 2500.0),
    }
    # OPS-03/FR-17: SEM o estado "alertado" do homelab — defaults do esquema.
    assert all(linha["direction"] == "below" for linha in linhas)
    assert all(linha["paused"] == 0 for linha in linhas)

    saida = capsys.readouterr().out
    assert "4" in saida
    assert "PETR4 (R$)" in saida
    assert "BTCUSDT (US$)" in saida
    assert "primeira rodada" in saida
    # o log final existe e só carrega a contagem (K2)
    assert "homelab_import" in saida
    assert "count=4" in saida


def test_banco_nao_vazio_recusado_sem_gravar_nada(settings, monkeypatch, tmp_path, capsys):
    """D-02: 1 watch pré-existente → recusa em PT-BR ANTES da write_tx —
    COUNT inalterado (a linha pré-existente segue sozinha, nada importado)."""
    _preenche_watch(settings)
    caminho = _cria_homelab(tmp_path)
    with pytest.raises(SystemExit) as saida:
        _roda_import(monkeypatch, caminho)
    assert saida.value.code != 0
    assert "lista vazia" in capsys.readouterr().out
    assert _contagem(settings, "watch") == 1  # nada foi importado (all-or-nothing)


def test_homelab_sem_tabela_esperada_recusado_antes_de_tocar_o_alvo(
    settings, monkeypatch, tmp_path, capsys
):
    """Homelab sem a tabela do mapeamento → recusa nomeando a tabela; o
    banco-alvo fica com COUNT de watch 0."""
    caminho = tmp_path / "stock.db"
    conn = sqlite3.connect(str(caminho))
    try:
        conn.executescript("CREATE TABLE frase_qualquer (id INTEGER)")  # sem `watch`
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(SystemExit) as saida:
        _roda_import(monkeypatch, caminho)
    assert saida.value.code != 0
    assert "'watch'" in capsys.readouterr().out
    assert _contagem(settings, "watch") == 0


def test_homelab_sem_coluna_do_mapeamento_recusada_antes_de_tocar_o_alvo(
    settings, monkeypatch, tmp_path, capsys
):
    """Tabela existe, coluna de alvo não → recusa nomeando a coluna; alvo
    intocado (COUNT de watch 0)."""
    caminho = _cria_homelab(tmp_path, insercoes=[], ddl=_DDL_SEM_COLUNA_ALVO)
    with pytest.raises(SystemExit) as saida:
        _roda_import(monkeypatch, caminho)
    assert saida.value.code != 0
    assert "'target'" in capsys.readouterr().out
    assert _contagem(settings, "watch") == 0


def test_par_duplicado_recusado_antes_de_gravar(settings, monkeypatch, tmp_path, capsys):
    """Par (símbolo, mercado) repetido no homelab → recusa ANTES de gravar
    (a PK (symbol, market) do alvo seria violada; a checagem é na leitura,
    não no INSERT): 0 linhas em watch. O DDL do fixture tira a PK do contrato
    — um homelab real pode não tê-la, e é por isso que o módulo valida."""
    caminho = _cria_homelab(
        tmp_path,
        ddl=_DDL_SEM_PK,
        insercoes=[
            ("PETR4", "b3", 32.5, 1),
            ("PETR4", "b3", 40.0, 0),  # par duplicado
            ("BTCUSDT", "crypto", 50000.0, 1),
        ],
    )
    with pytest.raises(SystemExit) as saida:
        _roda_import(monkeypatch, caminho)
    assert saida.value.code != 0
    assert "PETR4" in capsys.readouterr().out
    assert _contagem(settings, "watch") == 0


def test_alvo_invalido_recusado_antes_de_gravar(settings, monkeypatch, tmp_path, capsys):
    """Alvo <= 0 escapa do CHECK do contrato (PRAGMA de teste) → a validação
    em memória recusa ANTES de gravar: 0 linhas em watch (all-or-nothing)."""
    caminho = tmp_path / "stock.db"
    conn = sqlite3.connect(str(caminho))
    try:
        conn.executescript(_DDL_HOMELAB)
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("INSERT INTO watch (symbol, market, target) VALUES ('BBDC4', 'b3', 0)")
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(SystemExit) as saida:
        _roda_import(monkeypatch, caminho)
    assert saida.value.code != 0
    assert "BBDC4" in capsys.readouterr().out
    assert _contagem(settings, "watch") == 0


def test_homelab_inexistente_recusado_sem_tocar_o_alvo(settings, monkeypatch, tmp_path, capsys):
    """Caminho que não existe → recusa com mensagem clara; banco-alvo fica
    com COUNT de watch 0."""
    with pytest.raises(SystemExit) as saida:
        _roda_import(monkeypatch, tmp_path / "nao-existe.db")
    assert saida.value.code != 0
    assert "não encontrado" in capsys.readouterr().out
    assert _contagem(settings, "watch") == 0


def test_arquivo_nao_sqlite_recusado_sem_tocar_o_alvo(settings, monkeypatch, tmp_path, capsys):
    """Arquivo de texto no caminho → recusa nomeando que não é SQLite; alvo
    intocado (COUNT de watch 0)."""
    caminho = tmp_path / "texto.db"
    caminho.write_text("isto definitivamente não é um banco SQLite", encoding="utf-8")
    with pytest.raises(SystemExit) as saida:
        _roda_import(monkeypatch, caminho)
    assert saida.value.code != 0
    assert "SQLite" in capsys.readouterr().out
    assert _contagem(settings, "watch") == 0


def test_k2_nada_alem_de_simbolo_alvo_e_mercado_vaza_na_saida(
    settings, monkeypatch, tmp_path, capsys
):
    """K2/T-06-05: o homelab sintético carrega uma coluna extra FORA do
    mapeamento com um valor-fake parecido com token; stdout + captura de log
    NÃO contêm o valor nem o nome da coluna extra."""
    caminho = _cria_homelab(tmp_path, coluna_extra=_COLUNA_EXTRA)
    _roda_import(monkeypatch, caminho)

    captura = capsys.readouterr()
    tudo = captura.out + captura.err
    assert _TOKEN_FALSO not in tudo
    assert _COLUNA_EXTRA not in tudo
    # o import em si aconteceu (a recusa não mascara o K2)
    assert _contagem(settings, "watch") == 4


def test_insert_em_watch_nao_lista_direction_nem_paused():
    """OPS-03/FR-17: o INSERT do módulo NUNCA explicita direction nem paused
    — os defaults do esquema ('below'/0, migrações 0001/0004) é que dão item
    novo sem o estado alertado (grep do fonte assegura)."""
    fonte = Path(_modulo().__file__).read_text(encoding="utf-8")
    trecho = re.search(r"INSERT INTO watch\s*\([^)]*\)", fonte)
    assert trecho is not None, "INSERT INTO watch não encontrado no módulo"
    sql = trecho.group(0)
    assert "direction" not in sql
    assert "paused" not in sql


def test_modulo_na_fronteira_e_abre_homelab_somente_leitura():
    """Fronteira do worker (TID251): nenhum import de fastapi/pydantic no
    fonte; o banco do homelab é aberto por URI mode=ro — NUNCA pelo connect()
    do core, que ativaria pragmas de escrita/WAL em banco de esquema alheio
    (D-01)."""
    fonte = Path(_modulo().__file__).read_text(encoding="utf-8")
    proibidos = re.findall(r"^\s*(?:from|import)\s+(fastapi|pydantic)\b", fonte, re.MULTILINE)
    assert proibidos == []
    assert "mode=ro" in fonte
    assert "uri=True" in fonte


def test_banco_do_homelab_nunca_modificado(settings, monkeypatch, tmp_path):
    """T-06-04: o arquivo antigo sai byte a byte igual ao que entrou — e sem
    journal/WAL lado a lado (o modo ro não escreve no banco de outrem)."""
    caminho = _cria_homelab(tmp_path)
    antes = caminho.read_bytes()
    _roda_import(monkeypatch, caminho)
    assert caminho.read_bytes() == antes
    assert not (tmp_path / "stock.db-wal").exists()
    assert not (tmp_path / "stock.db-journal").exists()


def test_boot_idempotente_em_banco_ja_migrado(settings, monkeypatch, tmp_path):
    """Rodar o comando num banco-alvo JÁ migrado é no-op nas migrações e
    prossegue com o import (molde do boot: config → cheque → migrações)."""
    apply_migrations(settings.db_path)
    caminho = _cria_homelab(tmp_path)
    _roda_import(monkeypatch, caminho)
    assert _contagem(settings, "watch") == 4


def test_ajuda_imprime_uso_em_ptbr(monkeypatch, capsys):
    """--help imprime o uso com o argumento posicional e descrição PT-BR."""
    modulo = _modulo()
    monkeypatch.setattr(sys, "argv", ["baixou.worker.import_homelab", "--help"])
    with pytest.raises(SystemExit) as saida:
        modulo.main()
    assert saida.value.code == 0
    uso = capsys.readouterr().out
    assert "python -m baixou.worker.import_homelab" in uso
    assert "banco_homelab" in uso
    assert "homelab" in uso
