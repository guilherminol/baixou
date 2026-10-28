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
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


# --- 02-02 Task 3 (D-04, SC-2): espelho 451 com permanência e re-probe horário ---


def _hosts(requests: list[httpx.Request]) -> list[str]:
    return [r.url.host for r in requests]


def test_451_on_primary_fails_over_to_mirror_and_stays(capsys):
    """451 no principal → espelho assume e PERMANECE; mirror_fallback sai UMA vez."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.binance.com":
            return httpx.Response(451)
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "1.0"}])

    with _mock_client(handler, requests) as client:
        quotes, errors = binance.fetch_batch(client, ["BTCUSDT"])

    assert quotes["BTCUSDT"].price == 1.0  # a rodada grava as cotações
    assert errors == {}
    assert _hosts(requests) == ["api.binance.com", "data-api.binance.vision"]
    assert binance._mirror_state.using_mirror is True

    with _mock_client(handler, requests) as client:
        binance.fetch_batch(client, ["BTCUSDT"])  # permanência: nasce no espelho

    assert _hosts(requests)[2:] == ["data-api.binance.vision"]
    out = capsys.readouterr().out
    assert out.count("mirror_fallback") == 1  # evento único na troca, não por rodada


def test_transport_error_on_primary_also_fails_over_to_mirror(capsys):
    """Erro de conexão no principal dispara o mesmo caminho do 451 (D-04)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.binance.com":
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"symbol": "BTCUSDT", "price": "1.0"})

    with _mock_client(handler, requests) as client:
        quotes, errors = binance.fetch_batch(client, ["BTCUSDT"])

    assert quotes["BTCUSDT"].price == 1.0
    assert errors == {}
    assert _hosts(requests) == ["api.binance.com", "data-api.binance.vision"]
    assert capsys.readouterr().out.count("mirror_fallback") == 1


def test_mirror_reprobe_after_one_hour_recovers_primary(monkeypatch, capsys):
    """Depois de 1 h no espelho, UM re-probe tenta o principal; sucesso → mirror_recovery."""
    t0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    clock = {"now": t0}
    monkeypatch.setattr(binance, "_now", lambda: clock["now"])
    primary_ok = {"flag": False}
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.binance.com":
            if primary_ok["flag"]:
                return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "2.0"}])
            return httpx.Response(451)
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "1.0"}])

    with _mock_client(handler, requests) as client:
        binance.fetch_batch(client, ["BTCUSDT"])  # troca para o espelho
        clock["now"] = t0 + timedelta(minutes=30)
        binance.fetch_batch(client, ["BTCUSDT"])  # antes de 1 h: só espelho, sem probe
        assert _hosts(requests) == [
            "api.binance.com",
            "data-api.binance.vision",
            "data-api.binance.vision",
        ]

        primary_ok["flag"] = True
        clock["now"] = t0 + timedelta(minutes=61)
        quotes, _ = binance.fetch_batch(client, ["BTCUSDT"])  # re-probe: volta ao principal

    assert quotes["BTCUSDT"].price == 2.0  # a resposta vem do principal
    assert requests[-1].url.host == "api.binance.com"
    assert binance._mirror_state.using_mirror is False
    out = capsys.readouterr().out
    assert out.count("mirror_fallback") == 1
    assert out.count("mirror_recovery") == 1


def test_mirror_failed_reprobe_stays_on_mirror_for_another_hour(monkeypatch, capsys):
    """Re-probe falhando: permanece no espelho e o próximo re-probe é 1 h depois."""
    t0 = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
    clock = {"now": t0}
    monkeypatch.setattr(binance, "_now", lambda: clock["now"])
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.binance.com":
            return httpx.Response(451)  # principal segue bloqueado
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "1.0"}])

    with _mock_client(handler, requests) as client:
        binance.fetch_batch(client, ["BTCUSDT"])  # troca para o espelho
        clock["now"] = t0 + timedelta(minutes=61)
        binance.fetch_batch(client, ["BTCUSDT"])  # re-probe falha: espelho responde
        assert _hosts(requests)[-2:] == ["api.binance.com", "data-api.binance.vision"]
        assert binance._mirror_state.using_mirror is True

        clock["now"] = t0 + timedelta(minutes=65)  # probe acabou de falhar: sem probe
        binance.fetch_batch(client, ["BTCUSDT"])
        assert _hosts(requests)[-1] == "data-api.binance.vision"

        clock["now"] = t0 + timedelta(minutes=121)  # 1 h depois do probe falho: de novo
        binance.fetch_batch(client, ["BTCUSDT"])
        assert _hosts(requests)[-2:] == ["api.binance.com", "data-api.binance.vision"]

    assert binance._mirror_state.using_mirror is True
    out = capsys.readouterr().out
    assert out.count("mirror_fallback") == 1  # a troca foi UMA só, mesmo com re-probes


def test_mirror_state_is_memory_only():
    """D-04: nenhum schema para o endpoint ativo — migrações não citam espelho."""
    state = binance._mirror_state
    assert state.using_mirror is False  # reinício (módulo recarregado) volta ao principal
    assert state.last_primary_probe_at is None

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(migrations_dir.glob("*.sql")))
    assert "mirror" not in sql.lower()
    assert "binance.vision" not in sql.lower()  # nenhum endpoint ativo persistido
