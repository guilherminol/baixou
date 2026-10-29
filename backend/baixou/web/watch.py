"""Gestão da lista: GET /api/watch completo (plano 04-02, WATCH-01/WATCH-02).

A leitura junta watch com quote por LEFT JOIN fora de transação (molde do
/healthz: conexão por request, leitura simples sem lock) e deriva a moeda
pela função ÚNICA ``currency_for`` do núcleo comum — a mesma do aviso.
``notify_state`` chega do banco como TEXT JSON (formato congelado D-15 da
Fase 3) e é exposto PARSEADO: JSON ilegível vira null em vez de derrubar a
leitura, e nunca sai como string crua. Os verbos de escrita (POST, PATCH,
DELETE e o PUT em lote do D-06) entram nas Tasks 2–3 deste plano.
"""

from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Request

from baixou.core.currency import currency_for
from baixou.core.db import WEB_TIMEOUT_S, connect
from baixou.web.auth import Sessao
from baixou.web.schemas import WatchItem, WatchListResponse

router = APIRouter()

# LEFT JOIN: quote pode não existir (item ainda não consultado — e, após a
# migração 0004 do 04-03, cotação órfã sobrevive à remoção do item até o
# pruning do worker; D-01). Campos de quote NULL quando não há linha.
_SELECT_ITENS = """
    SELECT watch.symbol AS symbol, watch.market AS market,
           watch.target_below AS target_below,
           quote.price AS price, quote.price_at AS price_at,
           quote.change_pct AS change_pct, quote.alerted AS alerted,
           quote.alerted_at AS alerted_at, quote.notify_state AS notify_state
    FROM watch
    LEFT JOIN quote ON quote.symbol = watch.symbol AND quote.market = watch.market
    ORDER BY watch.market, watch.symbol
"""


def _abrir(request: Request) -> sqlite3.Connection:
    """Conexão por request (molde do /healthz), settings vindos do app.state."""
    settings = request.app.state.settings
    return connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)


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


@router.get("/api/watch", response_model=WatchListResponse)
def listar(request: Request, usuario: Sessao) -> WatchListResponse:
    """Itens da lista com cotação e moeda — exige sessão (del usuario: só autentica)."""
    del usuario
    conn = _abrir(request)
    try:
        return WatchListResponse(itens=_listar_itens(conn))
    finally:
        conn.close()
