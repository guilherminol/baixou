"""Fonte Binance com respostas gravadas — sem rede (POLL-01, POLL-02).

``httpx.MockTransport`` injetado no client (sem dependência nova): lote ok no
shape array (resposta de ``symbols``, JSON serializado na query), tolerância
ao shape objeto (resposta de ``symbol`` único), rejeição de shape inesperado
com falha tipada, quarentena do símbolo inválido (400 -1121 → 1-a-1 UMA vez,
D-02) e espelho 451 com re-probe horário (D-04).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

import httpx
import pytest
from baixou.worker.sources import binance
from baixou.worker.sources.base import (
    RateLimitError,
    UnexpectedResponseError,
)

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_Handler = Callable[[httpx.Request], httpx.Response]


def _mock_client(handler: _Handler, requests: list[httpx.Request]) -> httpx.Client:
    """Client com MockTransport que registra cada requisição disparada."""

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(recording))


def test_fetch_batch_happy_path_array_shape():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"symbol": "BTCUSDT", "price": "27000.10"},
                {"symbol": "ETHUSDT", "price": "1800.55"},
            ],
        )

    with _mock_client(handler, requests) as client:
        quotes, errors = binance.fetch_batch(client, ["BTCUSDT", "ETHUSDT"])

    request = requests[0]
    assert request.url.host == "api.binance.com"
    assert request.url.path == "/api/v3/ticker/price"
    assert json.loads(request.url.params["symbols"]) == ["BTCUSDT", "ETHUSDT"]
    assert set(quotes) == {"BTCUSDT", "ETHUSDT"}
    assert errors == {}  # lote único no caminho feliz: UMA requisição, sem 1-a-1
    assert len(requests) == 1
    assert quotes["BTCUSDT"].price == 27000.10  # preço string → float
    assert quotes["ETHUSDT"].change_pct is None  # o lote não traz variação
    datetime.strptime(quotes["BTCUSDT"].price_at, _TS_FORMAT)  # UTC ISO-8601


def test_fetch_batch_single_symbol_object_shape_is_tolerated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "27000.10"})

    with _mock_client(handler, []) as client:
        quotes, errors = binance.fetch_batch(client, ["BTCUSDT"])

    assert set(quotes) == {"BTCUSDT"}
    assert errors == {}
    assert quotes["BTCUSDT"].price == 27000.10


@pytest.mark.parametrize(
    "payload",
    [
        42,  # nem objeto nem array
        {"unexpected": True},  # objeto sem symbol/price
        [{"price": "1.0"}],  # entrada sem symbol
        [{"symbol": "BTCUSDT"}],  # entrada sem price
    ],
)
def test_fetch_batch_unexpected_shape_is_typed_unexpected(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _mock_client(handler, []) as client, pytest.raises(UnexpectedResponseError):
        binance.fetch_batch(client, ["BTCUSDT"])


def test_fetch_batch_unparseable_json_is_typed_unexpected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gateway</html>")

    with _mock_client(handler, []) as client, pytest.raises(UnexpectedResponseError):
        binance.fetch_batch(client, ["BTCUSDT"])


# --- 02-02 Task 2 (D-02): quarentena do símbolo inválido no lote ---


def _invalid_symbol_body() -> dict:
    return {"code": -1121, "msg": "Invalid symbol."}


def test_fetch_batch_400_invalid_symbol_retries_once_one_by_one():
    """400 -1121 → 1 lote + N singles UMA vez; o ruim sai como not_found (D-02)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "symbols" in request.url.params:
            return httpx.Response(400, json=_invalid_symbol_body())
        symbol = request.url.params["symbol"]
        if symbol == "BOGUS":
            return httpx.Response(400, json=_invalid_symbol_body())
        return httpx.Response(200, json={"symbol": symbol, "price": "1.5"})

    with _mock_client(handler, requests) as client:
        quotes, errors = binance.fetch_batch(client, ["BTCUSDT", "BOGUS", "ETHUSDT"])

    assert len(requests) == 4  # exatamente 1 lote + 3 chamadas 1-a-1 (nunca mais)
    batch_requests = [r for r in requests if "symbols" in r.url.params]
    single_requests = [r for r in requests if "symbol" in r.url.params]
    assert len(batch_requests) == 1
    assert {r.url.params["symbol"] for r in single_requests} == {"BTCUSDT", "BOGUS", "ETHUSDT"}
    assert set(quotes) == {"BTCUSDT", "ETHUSDT"}  # os válidos voltam normais
    assert quotes["BTCUSDT"].price == 1.5  # shape objeto parseado no 1-a-1
    assert set(errors) == {"BOGUS"}
    assert errors["BOGUS"].kind == "not_found"  # quarentena: gravado por item
    assert errors["BOGUS"].symbol == "BOGUS"


def test_fetch_batch_400_invalid_symbol_with_all_bad_returns_all_errors():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "symbols" in request.url.params:
            return httpx.Response(400, json=_invalid_symbol_body())
        return httpx.Response(400, json=_invalid_symbol_body())

    with _mock_client(handler, requests) as client:
        quotes, errors = binance.fetch_batch(client, ["BOGUS", "NOPE"])

    assert quotes == {}
    assert set(errors) == {"BOGUS", "NOPE"}
    assert all(exc.kind == "not_found" for exc in errors.values())


def test_fetch_batch_400_with_other_code_is_typed_unexpected():
    """400 com outro code nunca repete cegamente (Pitfall 2: 400 = consertar a chamada)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -1013, "msg": "Invalid message."})

    with _mock_client(handler, requests) as client, pytest.raises(UnexpectedResponseError):
        binance.fetch_batch(client, ["BTCUSDT"])

    assert len(requests) == 1  # sem tentativas extras


@pytest.mark.parametrize("status", [429, 418])
def test_fetch_batch_rate_limit_status_raises_without_extra_attempts(status):
    """429/418 → RateLimitError para o lote inteiro, sem retry e sem 1-a-1 (Pitfall 2)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"code": -1003, "msg": "Too much request weight."})

    with _mock_client(handler, requests) as client, pytest.raises(RateLimitError) as excinfo:
        binance.fetch_batch(client, ["BTCUSDT", "ETHUSDT"])

    assert excinfo.value.kind == "rate_limit"
    assert len(requests) == 1  # nenhuma repetição (evita ban de IP)
