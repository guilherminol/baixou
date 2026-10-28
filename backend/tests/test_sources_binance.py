"""Fonte Binance com respostas gravadas — sem rede (POLL-01, POLL-02).

``httpx.MockTransport`` injetado no client (sem dependência nova): lote ok no
shape array (resposta de ``symbols``, JSON serializado na query), tolerância
ao shape objeto (resposta de ``symbol`` único) e rejeição de shape inesperado
com falha tipada. Espelho 451 e quarentena entram no 02-02 (D-02/D-04).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime

import httpx
import pytest
from baixou.worker.sources import binance
from baixou.worker.sources.base import UnexpectedResponseError

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
        result = binance.fetch_batch(client, ["BTCUSDT", "ETHUSDT"])

    request = requests[0]
    assert request.url.host == "api.binance.com"
    assert request.url.path == "/api/v3/ticker/price"
    assert json.loads(request.url.params["symbols"]) == ["BTCUSDT", "ETHUSDT"]
    assert set(result) == {"BTCUSDT", "ETHUSDT"}
    assert result["BTCUSDT"].price == 27000.10  # preço string → float
    assert result["ETHUSDT"].change_pct is None  # o lote não traz variação
    datetime.strptime(result["BTCUSDT"].price_at, _TS_FORMAT)  # UTC ISO-8601


def test_fetch_batch_single_symbol_object_shape_is_tolerated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "27000.10"})

    with _mock_client(handler, []) as client:
        result = binance.fetch_batch(client, ["BTCUSDT"])

    assert set(result) == {"BTCUSDT"}
    assert result["BTCUSDT"].price == 27000.10


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
