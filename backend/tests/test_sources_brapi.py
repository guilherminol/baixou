"""Fonte brapi com respostas gravadas — sem rede (POLL-01, POLL-02).

``httpx.MockTransport`` injetado no client (sem dependência nova): caminho
feliz (parse de regularMarketPrice/regularMarketTime/regularMarketChangePercent),
token opcional (vazio → requisição sem o query param) e rejeição de shape
inesperado com falha tipada. Os mapeamentos de status HTTP entram no 02-02.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
from baixou.worker.sources import brapi
from baixou.worker.sources.base import SymbolNotFoundError, UnexpectedResponseError

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_Handler = Callable[[httpx.Request], httpx.Response]


def _mock_client(handler: _Handler, requests: list[httpx.Request]) -> httpx.Client:
    """Client com MockTransport que registra cada requisição disparada."""

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(recording))


def _happy_payload() -> dict:
    return {
        "results": [
            {
                "symbol": "PETR4",
                "regularMarketPrice": 38.25,
                "regularMarketTime": 1793154000,
                "regularMarketChangePercent": -1.3,
            }
        ]
    }


def test_fetch_one_happy_path_parses_brapi_fields():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_happy_payload())

    with _mock_client(handler, requests) as client:
        result = brapi.fetch_one(client, "PETR4", "")

    request = requests[0]
    assert request.url.host == "brapi.dev"
    assert request.url.path == "/api/quote/PETR4"
    assert "token" not in request.url.params  # token vazio → sem o query param
    assert result.symbol == "PETR4"
    assert result.price == 38.25
    assert result.change_pct == -1.3
    expected_ts = datetime.fromtimestamp(1793154000, tz=UTC).strftime(_TS_FORMAT)
    assert result.price_at == expected_ts  # epoch (s) → UTC ISO-8601


def test_fetch_one_with_token_sends_it_as_query_param():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_happy_payload())

    with _mock_client(handler, requests) as client:
        brapi.fetch_one(client, "PETR4", "tok-123")

    assert requests[0].url.params["token"] == "tok-123"


@pytest.mark.parametrize("payload", [{"results": []}, {"foo": "bar"}])
def test_fetch_one_results_empty_or_missing_is_symbol_not_found(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _mock_client(handler, []) as client, pytest.raises(SymbolNotFoundError):
        brapi.fetch_one(client, "PETR4", "")


@pytest.mark.parametrize(
    "payload",
    [
        "texto-na-raiz",  # JSON legível, shape raiz inesperado
        [{"symbol": "PETR4"}],  # array na raiz
        {"results": [{"symbol": "PETR4"}]},  # sem regularMarketPrice/regularMarketTime
        {"results": [{"regularMarketPrice": "38,25"}]},  # preço não numérico
    ],
)
def test_fetch_one_unexpected_shape_is_typed_unexpected(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with _mock_client(handler, []) as client, pytest.raises(UnexpectedResponseError):
        brapi.fetch_one(client, "PETR4", "")


def test_fetch_one_unparseable_json_is_typed_unexpected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="isto não é json")

    with _mock_client(handler, []) as client, pytest.raises(UnexpectedResponseError):
        brapi.fetch_one(client, "PETR4", "")
