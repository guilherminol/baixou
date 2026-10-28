"""Fonte brapi (B3): um símbolo por chamada, consultada em sequência (POLL-01, POLL-02).

Endereço fixo no código (evita SSRF — PROJECT.md Constraints). O token é
opcional: com valor vazio a requisição sai sem o query param. Sem retry em
laço — em limite de uso a rodada seguinte decide (arquitetura §Interfaces de
fontes; Pitfall 1: concorrência 1 da brapi, quem serializa é o chamador).
Nesta fatia (tracer) só o caminho feliz e o shape inesperado estão
implementados; os mapeamentos de status (401/403→auth, 404→not_found,
429→rate_limit, 5xx/rede→unavailable) entram no plano 02-02.
"""

from __future__ import annotations

import httpx

from baixou.worker.sources.base import (
    QuoteResult,
    SymbolNotFoundError,
    UnexpectedResponseError,
    utc_iso_from_epoch,
)

BRAPI_URL = "https://brapi.dev/api/quote/{symbol}"


def _is_number(value: object) -> bool:
    """Aceita int/float e rejeita bool (subclasse de int) — shape estrito."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def fetch_one(client: httpx.Client, symbol: str, token: str) -> QuoteResult:
    """Consulta UM ticker na brapi e devolve o resultado tipado (caminho feliz).

    ``token`` vazio → a requisição sai sem o query param (token opcional).
    Resultados vazios/ausentes viram ``SymbolNotFoundError``; JSON ilegível ou
    shape inesperado viram ``UnexpectedResponseError`` — a rodada nunca deixa
    a fonte derrubá-la com exceção fora do contrato (T-02-02).
    """
    params: dict[str, str] = {"token": token} if token else {}
    response = client.get(BRAPI_URL.format(symbol=symbol), params=params)
    try:
        payload = response.json()
    except ValueError as exc:
        raise UnexpectedResponseError(
            f"resposta ilegível da brapi (HTTP {response.status_code})", symbol=symbol
        ) from exc
    if not isinstance(payload, dict):
        raise UnexpectedResponseError("shape inesperado da resposta da brapi", symbol=symbol)
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        raise SymbolNotFoundError(f"símbolo sem resultados na brapi: {symbol}", symbol=symbol)
    quote = results[0]
    if not isinstance(quote, dict):
        raise UnexpectedResponseError("shape inesperado do resultado da brapi", symbol=symbol)
    price = quote.get("regularMarketPrice")
    epoch = quote.get("regularMarketTime")
    if not _is_number(price) or not _is_number(epoch):
        raise UnexpectedResponseError(
            "regularMarketPrice/regularMarketTime ausentes ou não numéricos", symbol=symbol
        )
    change_pct = quote.get("regularMarketChangePercent")
    if change_pct is not None and not _is_number(change_pct):
        raise UnexpectedResponseError("regularMarketChangePercent não numérico", symbol=symbol)
    return QuoteResult(
        symbol=symbol,
        price=float(price),
        price_at=utc_iso_from_epoch(epoch),
        change_pct=None if change_pct is None else float(change_pct),
    )
