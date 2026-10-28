"""Fonte Binance (cripto): um lote único por rodada (POLL-01, POLL-02).

Endereço fixo no código. A API exige a lista de símbolos serializada em JSON
na query (``symbols=["A","B"]``). O parser aceita o shape array (resposta de
``symbols``, caminho feliz do lote) e trata o shape objeto (resposta de
``symbol`` único) sem quebrar — a quarentena de símbolo inválido (D-02) e o
espelho 451 (D-04) entram no plano 02-02. O endpoint não devolve hora da
cotação nem variação: ``price_at`` é o instante da resposta (UTC) e
``change_pct`` é None.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from baixou.worker.sources.base import QuoteResult, UnexpectedResponseError

BINANCE_URL = "https://api.binance.com/api/v3/ticker/price"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def fetch_batch(client: httpx.Client, symbols: list[str]) -> dict[str, QuoteResult]:
    """Consulta os símbolos em UM lote e devolve ``{symbol: QuoteResult}``."""
    response = client.get(BINANCE_URL, params={"symbols": json.dumps(list(symbols))})
    try:
        payload = response.json()
    except ValueError as exc:
        raise UnexpectedResponseError(
            f"resposta ilegível da Binance (HTTP {response.status_code})"
        ) from exc
    if isinstance(payload, dict):
        # shape objeto: resposta de `symbol` único — tratada sem quebrar (02-02 a usa)
        entries = [payload]
    elif isinstance(payload, list):
        entries = payload  # shape array: resposta de `symbols` (caminho feliz do lote)
    else:
        raise UnexpectedResponseError("shape inesperado do lote da Binance")
    price_at = datetime.now(UTC).strftime(_TS_FORMAT)
    results: dict[str, QuoteResult] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise UnexpectedResponseError("entrada do lote sem shape de ticker")
        symbol = entry.get("symbol")
        raw_price = entry.get("price")
        price_ok = isinstance(raw_price, (str, int, float)) and not isinstance(raw_price, bool)
        if not isinstance(symbol, str) or not price_ok:
            raise UnexpectedResponseError("entrada do lote sem symbol/price válidos")
        try:
            price = float(raw_price)  # Binance devolve o preço como string
        except ValueError as exc:
            raise UnexpectedResponseError(f"preço ilegível da Binance: {raw_price!r}") from exc
        results[symbol] = QuoteResult(
            symbol=symbol, price=price, price_at=price_at, change_pct=None
        )
    return results
