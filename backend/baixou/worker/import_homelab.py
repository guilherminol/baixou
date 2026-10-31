"""Importação da lista do homelab para o banco do Baixou (OPS-03, FR-17).

Uso (documentado no README)::

    docker compose -f compose.install.yaml cp ./stock.db worker:/mnt/homelab.db
    docker compose -f compose.install.yaml exec worker \
        python -m baixou.worker.import_homelab /mnt/homelab.db

    # alternativa sem copiar — montagem somente-leitura num container descartável:
    docker compose -f compose.install.yaml run --rm -v ./stock.db:/mnt/homelab.db:ro \
        worker python -m baixou.worker.import_homelab /mnt/homelab.db

Traz tickers e alvos do banco antigo do homelab para a tabela ``watch`` SEM o
estado "alertado" (OPS-03/FR-17): as linhas nascem com ``direction='below'`` e
``paused=0`` — defaults do esquema (migrações 0001/0004), NUNCA colunas
explicitadas com valor do homelab — e a primeira rodada do worker recalcula os
estados. Itens que já estão abaixo do alvo disparam um aviso nessa primeira
rodada: esperado.

O import SÓ roda em banco vazio (D-02): qualquer watch pré-existente sai com
erro ANTES de gravar — o fluxo de adoção é subir → setup → importar → usar.
Nada é gravado parcialmente: leitura e validação do homelab acontecem FORA de
transação (pares (símbolo, mercado) únicos, alvo numérico > 0) e a gravação é
UMA ``write_tx`` all-or-nothing — qualquer violação sai com ``SystemExit(1)``
e COUNT de watch inalterado. Símbolos NÃO revalidam na brapi/Binance (D-04):
a primeira rodada valida de graça e a quarentena ``not_found`` isola o
inválido; gastar cota seria redundante.

Mapeamento de colunas (D-01): deriva do CONTRATO documentado em
``.planning/phases/06-empacotamento-release-e-paridade-com-o-homelab/homelab-schema.sql``
— autorado do zero por decisão do usuário no checkpoint de 30/10/2026 (o
banco do homelab está vazio; se um homelab real surgir com colunas
diferentes, o mapeamento deve ser revisitado). O contrato TEM coluna de
mercado, então os valores são validados ao par ('b3', 'crypto'); se um
homelab sem coluna de mercado aparecer, derivar pela mesma fronteira do
``currency_for`` (sufixo/par cripto → 'crypto', resto → 'b3') e documentar a
regra aqui. As colunas ``alerted`` (estado "já alertei" do homelab) e
``atualizado_em`` existem no contrato e ficam FORA do mapeamento de propósito.

K2 (T-06-05): nada do banco antigo além de símbolo, alvo e mercado aparece na
saída, no log ou em erro — o log final carrega só a contagem. O banco do
homelab é aberto SOMENTE-LEITURA por URI (``mode=ro``) — NUNCA por
``baixou.core.db.connect``, que ativaria pragmas de escrita/WAL em banco de
esquema alheio (T-06-04). Stdlib puro + ``baixou.core``: a fronteira do
worker (TID251) reprova fastapi/pydantic.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from baixou.core.config import Settings, load_settings
from baixou.core.currency import currency_for
from baixou.core.db import WORKER_TIMEOUT_S, check_sqlite_version, connect, write_tx
from baixou.core.log import configure, log
from baixou.core.migrations import apply_migrations

# Mapeamento de colunas (D-01) — tabela e colunas do homelab segundo o
# CONTRATO de .planning/phases/06-empacotamento-release-e-paridade-com-o-homelab/
# homelab-schema.sql (autorado do zero por decisão do usuário no checkpoint
# de 30/10/2026 — o banco do homelab está vazio).
HOMELAB_TABLE = "watch"
HOMELAB_COL_SYMBOL = "symbol"
HOMELAB_COL_MARKET = "market"
HOMELAB_COL_TARGET = "target"

# INSERT SEM direction e SEM paused: os defaults do esquema ('below'/0,
# migrações 0001/0004) é que dão item novo sem o estado alertado (OPS-03).
_INSERT_WATCH = "INSERT INTO watch (symbol, market, target_below) VALUES (?, ?, ?)"


@dataclass(frozen=True)
class ItemHomelab:
    """Item lido do banco do homelab — só as três colunas do mapeamento."""

    symbol: str
    market: str
    target: float


def _build_parser() -> argparse.ArgumentParser:
    """O caminho do banco do homelab é o ÚNICO argumento (posicional)."""
    parser = argparse.ArgumentParser(
        prog="python -m baixou.worker.import_homelab",
        description=(
            "Importa a lista de itens do homelab (símbolo, alvo e mercado) "
            "para o Baixou — somente em banco vazio e sem o estado 'alertado' "
            "do homelab: a primeira rodada recalcula tudo."
        ),
    )
    parser.add_argument(
        "banco_homelab",
        help="caminho do arquivo SQLite do homelab (aberto somente-leitura)",
    )
    return parser


def _abre_homelab(caminho: str) -> sqlite3.Connection:
    """Abre o banco do homelab SOMENTE-LEITURA por URI (``mode=ro``, D-01).

    NUNCA ``baixou.core.db.connect`` aqui: ele ativa pragmas de escrita/WAL
    em banco de esquema alheio. O modo ro torna o arquivo antigo FISICAMENTE
    imutável (T-06-04) — qualquer tentativa de escrita falha.
    """
    uri = f"file:{quote(Path(caminho).resolve().as_posix(), safe=':/')}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _le_itens(caminho: str) -> list[ItemHomelab]:
    """Lê e valida os itens do homelab FORA de transação, sem tocar o alvo.

    O PRAGMA table_info prova que a tabela e TODAS as colunas do mapeamento
    existem ANTES de ler linhas; a validação em memória (mercado no par
    ('b3', 'crypto'), alvo numérico > 0, pares únicos) roda ANTES de qualquer
    escrita — all-or-nothing (D-02). Mensagens citam só símbolo, mercado e
    alvo: nada mais do banco antigo vaza (K2).
    """
    arquivo = Path(caminho)
    if not arquivo.exists():
        print(f"arquivo do homelab não encontrado: {arquivo}")
        raise SystemExit(1)

    conn: sqlite3.Connection | None = None
    try:
        try:
            conn = _abre_homelab(caminho)
            colunas = {
                linha["name"] for linha in conn.execute(f'PRAGMA table_info("{HOMELAB_TABLE}")')
            }
            if not colunas:
                print(f"o banco do homelab não tem a tabela esperada: '{HOMELAB_TABLE}'")
                raise SystemExit(1)
            faltosas = [
                coluna
                for coluna in (HOMELAB_COL_SYMBOL, HOMELAB_COL_MARKET, HOMELAB_COL_TARGET)
                if coluna not in colunas
            ]
            if faltosas:
                print(
                    f"a tabela '{HOMELAB_TABLE}' do homelab não tem a coluna "
                    f"esperada do mapeamento: '{faltosas[0]}'"
                )
                raise SystemExit(1)
            linhas = conn.execute(
                f'SELECT "{HOMELAB_COL_SYMBOL}", "{HOMELAB_COL_MARKET}", '
                f'"{HOMELAB_COL_TARGET}" FROM "{HOMELAB_TABLE}"'
            ).fetchall()
        except sqlite3.DatabaseError:
            # from None: a exceção sqlite carrega detalhes internos do arquivo
            # alheio — a mensagem PT-BR limpa é a única saída (K2).
            print(f"o arquivo do homelab não pôde ser lido como banco SQLite: {arquivo}")
            raise SystemExit(1) from None
    finally:
        if conn is not None:
            conn.close()

    itens: list[ItemHomelab] = []
    vistos: set[tuple[str, str]] = set()
    for linha in linhas:
        symbol = str(linha[HOMELAB_COL_SYMBOL]).strip()
        market = str(linha[HOMELAB_COL_MARKET]).strip()
        target = linha[HOMELAB_COL_TARGET]
        if not symbol:
            print("item do homelab sem símbolo — corrija o banco antigo")
            raise SystemExit(1)
        if market not in ("b3", "crypto"):
            print(
                f"mercado desconhecido '{market}' para o item {symbol} — esperado 'b3' ou 'crypto'"
            )
            raise SystemExit(1)
        if not isinstance(target, (int, float)) or target <= 0:
            print(
                f"alvo inválido para o item {symbol} ({market}) — o alvo "
                "precisa ser um número maior que zero"
            )
            raise SystemExit(1)
        par = (symbol, market)
        if par in vistos:
            print(f"item duplicado no homelab: {symbol} ({market})")
            raise SystemExit(1)
        vistos.add(par)
        itens.append(ItemHomelab(symbol=symbol, market=market, target=float(target)))
    return itens


def main() -> None:
    """Boot no molde do worker; leitura do homelab e gravação all-or-nothing."""
    args = _build_parser().parse_args()
    settings: Settings = load_settings()
    configure(settings.log_level)
    check_sqlite_version()
    apply_migrations(settings.db_path)

    itens = _le_itens(args.banco_homelab)

    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"]
        if total > 0:
            # D-02: import SÓ em banco vazio — o erro sai ANTES da write_tx e
            # NADA é gravado (fluxo de adoção: subir → setup → importar → usar).
            print(
                f"o import só roda com a lista vazia: já existe(m) {total} "
                "item(ns) cadastrado(s) — remova-o(s) ou comece com um banco novo"
            )
            raise SystemExit(1)
        with write_tx(conn):
            # UMA write_tx all-or-nothing; direction/paused NUNCA explícitos —
            # nascem dos defaults 'below'/0 do esquema (OPS-03/FR-17).
            conn.executemany(
                _INSERT_WATCH,
                [(item.symbol, item.market, item.target) for item in itens],
            )
    finally:
        conn.close()

    # Relatório (D-04): total + itens por mercado com a moeda derivada de
    # currency_for — e a linha que a arquitetura exige. NENHUM símbolo
    # revalida na brapi/Binance aqui: a 1ª rodada valida de graça.
    print(f"Importação concluída: {len(itens)} item(ns) do homelab.")
    for market in ("b3", "crypto"):
        do_mercado = [item for item in itens if item.market == market]
        if not do_mercado:
            continue
        detalhes = ", ".join(
            f"{item.symbol} ({currency_for(item.symbol, item.market)})" for item in do_mercado
        )
        print(f"- {market}: {len(do_mercado)} item(ns) — {detalhes}")
    print(
        "Itens que já estão abaixo do alvo disparam um aviso na primeira rodada — isso é esperado."
    )

    # Log final K2: só a contagem, nenhum dado do banco antigo.
    log(
        "info",
        "worker",
        "homelab_import",
        count=len(itens),
        text="lista do homelab importada",
    )


if __name__ == "__main__":
    main()
