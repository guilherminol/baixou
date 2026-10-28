"""Fonte brapi com respostas gravadas — sem rede (POLL-01, POLL-02).

``httpx.MockTransport`` injetado no client (sem dependência nova): caminho
feliz (parse de regularMarketPrice/regularMarketTime/regularMarketChangePercent),
token opcional (vazio → requisição sem o query param) e rejeição de shape
inesperado com falha tipada. Os 5 kinds de status HTTP (02-02): 401/403→auth,
404/vazio→not_found, 429→rate_limit sem retry em laço, 5xx/rede→unavailable,
JSON ilegível→unexpected — nenhum erro carrega a URL (que levaria o token).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
from baixou.worker.sources import brapi
from baixou.worker.sources.base import (
    AuthError,
    RateLimitError,
    SourceUnavailableError,
    SymbolNotFoundError,
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


# --- 02-02: mapeamento de status → falha tipada (SC-3), sem vazar URL/token ---


@pytest.mark.parametrize("status", [401, 403])
def test_fetch_one_auth_status_is_typed_auth(status):
    """401/403 → AuthError(kind='auth'); a mensagem cita o código, nunca a URL."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "invalid token"})

    with _mock_client(handler, requests) as client, pytest.raises(AuthError) as excinfo:
        brapi.fetch_one(client, "PETR4", "tok-123")

    assert excinfo.value.kind == "auth"
    assert excinfo.value.symbol == "PETR4"
    message = str(excinfo.value)
    assert str(status) in message  # código HTTP citado
    assert "brapi.dev" not in message and "tok-123" not in message  # K2: sem URL/token


def test_fetch_one_404_is_symbol_not_found():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    with _mock_client(handler, requests) as client, pytest.raises(SymbolNotFoundError) as excinfo:
        brapi.fetch_one(client, "PETR4", "")

    assert excinfo.value.kind == "not_found"


def test_fetch_one_429_is_rate_limit_with_exactly_one_request():
    """429 (com Retry-After) → RateLimitError SEM repetir a chamada (Pitfall 1)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"message": "too many"})

    with _mock_client(handler, requests) as client, pytest.raises(RateLimitError) as excinfo:
        brapi.fetch_one(client, "PETR4", "")

    assert excinfo.value.kind == "rate_limit"
    assert len(requests) == 1  # sem retry em laço: a rodada seguinte decide


@pytest.mark.parametrize("status", [500, 503])
def test_fetch_one_server_error_is_unavailable(status):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="boom")

    with (
        _mock_client(handler, requests) as client,
        pytest.raises(SourceUnavailableError) as excinfo,
    ):
        brapi.fetch_one(client, "PETR4", "")

    assert excinfo.value.kind == "unavailable"
    assert str(status) in str(excinfo.value)


def test_fetch_one_transport_error_is_unavailable_without_url():
    """Erro de transporte → SourceUnavailableError; a URL (com token=) não vaza."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with (
        _mock_client(handler, requests) as client,
        pytest.raises(SourceUnavailableError) as excinfo,
    ):
        brapi.fetch_one(client, "PETR4", "tok-123")

    assert excinfo.value.kind == "unavailable"
    message = str(excinfo.value)
    assert "brapi.dev" not in message  # a URL nunca entra na exceção propagada (K2)
    assert "tok-123" not in message and "token=" not in message
