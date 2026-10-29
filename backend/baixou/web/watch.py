"""Gestão da lista: GET/POST/PATCH/DELETE /api/watch (plano 04-02, WATCH-01/WATCH-02).

A leitura junta watch com quote por LEFT JOIN fora de transação (molde do
/healthz: conexão por request, leitura simples sem lock) e deriva a moeda
pela função ÚNICA ``currency_for`` do núcleo comum — a mesma do aviso.
``notify_state`` chega do banco como TEXT JSON (formato congelado D-15 da
Fase 3) e é exposto PARSEADO: JSON ilegível vira null em vez de derrubar a
leitura, e nunca sai como string crua.

Escrita item a item com o pipeline fixo (D-02/D-03/D-04/D-05): normaliza o
símbolo para maiúsculas, valida a forma localmente (alvo > 0, duplicado por
SELECT ANTES de qualquer rede), valida o símbolo na fonte (rede FORA de
transação — disciplina WAL) e SÓ ENTÃO abre UMA ``write_tx`` curta. Recusas
saem no envelope D-08 (422) nomeando ticker/campo/problema; indisponibilidade
é 503 "tente de novo" — nunca 422. O PUT em lote (D-06/D-07) entra na Task 3.
"""

from __future__ import annotations

import json
import sqlite3

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from baixou.core.currency import currency_for
from baixou.core.db import WEB_TIMEOUT_S, connect, write_tx
from baixou.web import symbol_validation
from baixou.web.auth import Sessao
from baixou.web.schemas import PatchWatch, WatchItem, WatchListResponse, erro_item
from baixou.worker.sources.base import SourceError, SymbolNotFoundError

router = APIRouter()

# LEFT JOIN: quote pode não existir (item ainda não consultado — e, após a
# migração 0004 do 04-03, cotação órfã sobrevive à remoção do item até o
# pruning do worker; D-01). Campos de quote NULL quando não há linha.
# A base sai SEM ordenação: o GET acrescenta ORDER BY e o item único, WHERE.
_SELECT_ITENS_BASE = """
    SELECT watch.symbol AS symbol, watch.market AS market,
           watch.target_below AS target_below,
           quote.price AS price, quote.price_at AS price_at,
           quote.change_pct AS change_pct, quote.alerted AS alerted,
           quote.alerted_at AS alerted_at, quote.notify_state AS notify_state
    FROM watch
    LEFT JOIN quote ON quote.symbol = watch.symbol AND quote.market = watch.market
"""
_SELECT_ITENS = _SELECT_ITENS_BASE + " ORDER BY watch.market, watch.symbol"

# D-02: (re)criação SEMPRE começa ciclo NOVO — a quote é sobreposta com o
# estado de alerta zerado (a quarentena da Fase 2, last_fetch_error, é limpa
# por aqui). O ON CONFLICT cobre a cotação órfã que sobrevive à remoção
# quando a migração 0004 do 04-03 retirar a cascata do esquema.
_RESET_QUOTE = """
    INSERT INTO quote (symbol, market) VALUES (?, ?)
    ON CONFLICT (symbol, market) DO UPDATE SET
        price = NULL, price_at = NULL, change_pct = NULL,
        alerted = 0, alerted_at = NULL, alerted_target = NULL,
        notify_state = NULL, last_fetch_error = NULL
"""

_MSG_ALVO = "o alvo deve ser maior que zero"
_MSG_DUPLICADO = "o símbolo já está na lista"
_MSG_INEXISTENTE_FONTE = "símbolo não encontrado na fonte"
_MSG_ITEM_NAO_ENCONTRADO = "item não encontrado"
_MSG_FONTE_INDISPONIVEL = "fonte indisponível para validar o símbolo — tente de novo"


def _abrir(request: Request) -> sqlite3.Connection:
    """Conexão por request (molde do /healthz), settings vindos do app.state."""
    settings = request.app.state.settings
    return connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)


def _normalizar(symbol: str) -> str:
    """D-05: 'petr4' e 'PETR4' são o MESMO item — maiúsculas, sem espaços."""
    return symbol.strip().upper()


def _recusa(symbol: str | None, campo: str, problema: str) -> JSONResponse:
    """Envelope D-08 com UMA recusa nomeando ticker/campo/problema (PT-BR)."""
    return JSONResponse(status_code=422, content={"erros": [erro_item(symbol, campo, problema)]})


def _cliente_validacao() -> httpx.Client:
    """Cliente da validação de símbolo (emenda de teste: MockTransport nos testes)."""
    return symbol_validation.novo_cliente()


def _validar_na_fonte(settings, symbol: str, market: str) -> None:
    """Valida o símbolo na fonte FORA de transação; rede nunca segura o lock.

    ``SymbolNotFoundError`` (422) e ``SourceError`` (503) propagam tipados —
    o cliente HTTP é fechado sempre.
    """
    cliente = _cliente_validacao()
    try:
        symbol_validation.validar(cliente, settings, symbol, market)
    finally:
        cliente.close()


def _notify_state_parseado(cru: object) -> dict | None:
    """JSON TEXT → objeto; ilegível ou não-objeto → null (nunca derruba a leitura)."""
    if cru is None:
        return None
    try:
        parsed = json.loads(str(cru))
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _item_de_linha(row: sqlite3.Row) -> WatchItem:
    """Linha watch⋈quote → WatchItem com a moeda da fonte única de verdade."""
    return WatchItem(
        symbol=row["symbol"],
        market=row["market"],
        target_below=row["target_below"],
        currency=currency_for(row["symbol"], row["market"]),
        price=row["price"],
        price_at=row["price_at"],
        change_pct=row["change_pct"],
        alerted=bool(row["alerted"]),
        alerted_at=row["alerted_at"],
        notify_state=_notify_state_parseado(row["notify_state"]),
    )


def _listar_itens(conn: sqlite3.Connection) -> list[WatchItem]:
    """Itens completos e ordenados — a MESMA leitura serve ao GET e ao PUT."""
    return [_item_de_linha(row) for row in conn.execute(_SELECT_ITENS).fetchall()]


def _buscar_item(conn: sqlite3.Connection, symbol: str, market: str) -> WatchItem | JSONResponse:
    """UM item completo pela MESMA leitura do GET; sumiu → 404 (nada a devolver)."""
    sql = _SELECT_ITENS_BASE + " WHERE watch.symbol = ? AND watch.market = ?"
    row = conn.execute(sql, (symbol, market)).fetchone()
    if row is None:
        return JSONResponse(status_code=404, content={"detail": _MSG_ITEM_NAO_ENCONTRADO})
    return _item_de_linha(row)


def _existe(conn: sqlite3.Connection, symbol: str, market: str) -> bool:
    """Duplicado por SELECT — recusa local ANTES de qualquer rede."""
    return (
        conn.execute(
            "SELECT 1 FROM watch WHERE symbol = ? AND market = ?", (symbol, market)
        ).fetchone()
        is not None
    )


@router.get("/api/watch", response_model=WatchListResponse)
def listar(request: Request, usuario: Sessao) -> WatchListResponse:
    """Itens da lista com cotação e moeda — exige sessão (del usuario: só autentica)."""
    del usuario
    conn = _abrir(request)
    try:
        return WatchListResponse(itens=_listar_itens(conn))
    finally:
        conn.close()


@router.post("/api/watch", status_code=201, response_model=WatchItem)
def cadastrar(request: Request, corpo: WatchItem, usuario: Sessao) -> WatchItem | JSONResponse:
    """Cadastra um item NOVO com validação item a item (SC-4, D-02..D-05).

    Ordem do pipeline: normaliza (D-05) → válidos locais (alvo > 0 conferido
    no endpoint além do gt=0 do modelo; duplicado por SELECT) → fonte com
    rede FORA de transação → write_tx ÚNICA gravando watch e sobrepondo a
    quote com ciclo NOVO (D-02). A recusa nunca chega a abrir transação.
    """
    del usuario
    symbol = _normalizar(corpo.symbol)
    if not symbol:
        return _recusa(None, "symbol", "informe o símbolo do ativo")
    if not corpo.target_below > 0:  # conferência do gt=0 (defesa em profundidade)
        return _recusa(symbol, "target_below", _MSG_ALVO)
    conn = _abrir(request)
    try:
        if _existe(conn, symbol, corpo.market):
            return _recusa(symbol, "symbol", _MSG_DUPLICADO)
        try:
            _validar_na_fonte(request.app.state.settings, symbol, corpo.market)
        except SymbolNotFoundError:
            return _recusa(symbol, "symbol", _MSG_INEXISTENTE_FONTE)
        except SourceError:
            return JSONResponse(status_code=503, content={"detail": _MSG_FONTE_INDISPONIVEL})
        with write_tx(conn):
            conn.execute(
                "INSERT INTO watch (symbol, market, target_below) VALUES (?, ?, ?)",
                (symbol, corpo.market, corpo.target_below),
            )
            conn.execute(_RESET_QUOTE, (symbol, corpo.market))  # ciclo NOVO (D-02)
        return _buscar_item(conn, symbol, corpo.market)
    finally:
        conn.close()


@router.patch("/api/watch/{symbol}/{market}", response_model=WatchItem)
def editar(
    request: Request, symbol: str, market: str, corpo: PatchWatch, usuario: Sessao
) -> WatchItem | JSONResponse:
    """Muda SÓ o alvo do item — sem revalidar na fonte (o item já existe)."""
    del usuario
    symbol = _normalizar(symbol)
    conn = _abrir(request)
    try:
        if not _existe(conn, symbol, market):
            return JSONResponse(status_code=404, content={"detail": _MSG_ITEM_NAO_ENCONTRADO})
        with write_tx(conn):
            conn.execute(
                "UPDATE watch SET target_below = ? WHERE symbol = ? AND market = ?",
                (corpo.target_below, symbol, market),
            )
        return _buscar_item(conn, symbol, market)
    finally:
        conn.close()


@router.delete("/api/watch/{symbol}/{market}", status_code=204)
def remover(request: Request, symbol: str, market: str, usuario: Sessao):  # noqa: ANN201 — 204 sem corpo
    """Remove SÓ a linha de watch (D-01): a cotação fica para o pruning do worker.

    Nesta task o esquema 0001 ainda apaga a quote junto (ON DELETE CASCADE) —
    a preservação da cotação órfã é prova do plano 04-03, após a migração 0004.
    """
    del usuario
    symbol = _normalizar(symbol)
    conn = _abrir(request)
    try:
        with write_tx(conn):
            apagadas = conn.execute(
                "DELETE FROM watch WHERE symbol = ? AND market = ?", (symbol, market)
            ).rowcount
        if apagadas == 0:
            return JSONResponse(status_code=404, content={"detail": _MSG_ITEM_NAO_ENCONTRADO})
    finally:
        conn.close()
    return Response(status_code=204)
