"""Fonte brapi (B3): um símbolo por chamada, consultada em sequência (POLL-01, POLL-02).

Endereço fixo no código (evita SSRF — PROJECT.md Constraints). O token é
opcional: com valor vazio a requisição sai sem o query param. Sem retry em
laço — em limite de uso a rodada seguinte decide (arquitetura §Interfaces de
fontes; Pitfall 1: concorrência 1 da brapi, quem serializa é o chamador).
Status → falha tipada (SC-3): 401/403→auth, 404/vazio→not_found,
429→rate_limit (sem repetir), 5xx/rede→unavailable, JSON ilegível→unexpected.
Em nenhum caminho de erro a URL entra no log ou na exceção propagada: as
exceções httpx carregam a requisição completa e o token da brapi viaja na
query (token=...) — as mensagens citam apenas o kind e o código HTTP (K2,
T-02-04).
"""

from __future__ import annotations

import httpx

from baixou.worker.sources.base import (
    AuthError,
    QuoteResult,
    RateLimitError,
    SourceUnavailableError,
    SymbolNotFoundError,
    UnexpectedResponseError,
    utc_iso_from_epoch,
)

BRAPI_URL = "https://brapi.dev/api/quote/{symbol}"

_AUTH_STATUS = frozenset({401, 403})
_NOT_FOUND_STATUS = frozenset({404})
_RATE_LIMIT_STATUS = frozenset({429})


def _is_number(value: object) -> bool:
    """Aceita int/float e rejeita bool (subclasse de int) — shape estrito."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _raise_for_status(response: httpx.Response, symbol: str) -> None:
    """Mapeia status HTTP para a falha tipada, citando só kind e código (K2)."""
    status = response.status_code
    if status in _AUTH_STATUS:
        raise AuthError(f"brapi recusou a autenticação (HTTP {status})", symbol=symbol)
    if status in _NOT_FOUND_STATUS:
        raise SymbolNotFoundError(f"símbolo não encontrado na brapi (HTTP {status})", symbol=symbol)
    if status in _RATE_LIMIT_STATUS:
        # Sem retry em laço quando limite de uso: respeitar o espírito do
        # Retry-After é não insistir — a rodada seguinte tenta de novo.
        raise RateLimitError(f"limite de uso da brapi atingido (HTTP {status})", symbol=symbol)
    if status >= 500:
        raise SourceUnavailableError(f"brapi indisponível (HTTP {status})", symbol=symbol)


def fetch_one(client: httpx.Client, symbol: str, token: str) -> QuoteResult:
    """Consulta UM ticker na brapi e devolve o resultado tipado.

    ``token`` vazio → a requisição sai sem o query param (token opcional).
    Falhas tipadas (SC-3): status mapeado por ``_raise_for_status``, resultados
    vazios/ausentes viram ``SymbolNotFoundError``, JSON ilegível ou shape
    inesperado viram ``UnexpectedResponseError`` e erro de transporte vira
    ``SourceUnavailableError`` — a rodada nunca deixa a fonte derrubá-la com
    exceção fora do contrato (T-02-02). A exceção httpx original não é
    encadeada nos erros de rede/requisição: ela carrega a URL com o token na
    query, e a URL nunca pode escapar por um traceback (K2, T-02-04).
    """
    params: dict[str, str] = {"token": token} if token else {}
    try:
        response = client.get(BRAPI_URL.format(symbol=symbol), params=params)
    except httpx.TransportError as exc:
        raise SourceUnavailableError(
            f"erro de rede na brapi: {type(exc).__name__}", symbol=symbol
        ) from None
    _raise_for_status(response, symbol)
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
