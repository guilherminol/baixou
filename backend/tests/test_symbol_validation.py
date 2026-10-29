"""Validação híbrida de símbolo (D-03/D-04) — MockTransport, zero rede.

Molde test_sources_binance.py: assert no shape do request e mapeamento
kind → exceção tipada. O status HTTP (422/503) é decisão da rota; o
contrato aqui é ``validar`` levantar ``SymbolNotFoundError`` para
inexistente e ``SourceUnavailableError`` para fonte/rede — e NEM CONSULTAR
a brapi fora do pregão (a 1ª rodada valida de fato, D-03).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from dataclasses import replace

import httpx
import pytest
from baixou.web.symbol_validation import validar
from baixou.worker.sources.base import SourceUnavailableError, SymbolNotFoundError

_Handler = Callable[[httpx.Request], httpx.Response]

# Quarta 16:00 UTC = 13:00 em Brasília: dentro do pregão B3 (molde test_round).
_ABERTO = datetime(2026, 10, 28, 16, 0, 0, tzinfo=UTC)
# Domingo 15:00 UTC = 12:00 em Brasília: fora do pregão (SC-1).
_FECHADO = datetime(2026, 11, 1, 15, 0, 0, tzinfo=UTC)


def _mock_client(handler: _Handler, requests: list[httpx.Request]) -> httpx.Client:
    """Client com MockTransport que registra cada requisição disparada."""

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(recording))


def _congela_relogio(monkeypatch, now_utc: datetime) -> None:
    """Fixa o ``datetime`` do módulo de validação num instante arbitrário."""

    class _Congelado(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return now_utc.astimezone(tz)
            return now_utc.replace(tzinfo=None)

    from baixou.web import symbol_validation

    monkeypatch.setattr(symbol_validation, "datetime", _Congelado)


def _violento(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"rede não deveria ter sido chamada: {request.url.host}")


def test_validar_cripto_existente_nao_levanta(settings):
    """Símbolo no lote da Binance → None (aceito); UM lote único, sem 1-a-1."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"symbol": "SOLUSDT", "price": "96.5"}])

    with _mock_client(handler, requests) as client:
        assert validar(client, settings, "SOLUSDT", "crypto") is None
    assert len(requests) == 1
    assert json.loads(requests[0].url.params["symbols"]) == ["SOLUSDT"]


def test_validar_cripto_inexistente_levanta_not_found(settings):
    """400 -1121 → 1-a-1 → o ruim sai como SymbolNotFoundError (422 na rota)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})

    with _mock_client(handler, requests) as client:
        with pytest.raises(SymbolNotFoundError) as excinfo:
            validar(client, settings, "BOGUS", "crypto")
    assert excinfo.value.symbol == "BOGUS"
    assert excinfo.value.kind == "not_found"


def test_validar_cripto_fonte_nivel_levanta_indisponivel(settings):
    """429 (nível da fonte) → SourceUnavailableError (503 na rota, D-04)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"code": -1003, "msg": "Too much request weight."})

    with _mock_client(handler, requests) as client:
        with pytest.raises(SourceUnavailableError) as excinfo:
            validar(client, settings, "BTCUSDT", "crypto")
    assert excinfo.value.kind == "unavailable"
    assert len(requests) == 1  # sem retry em laço (Pitfall 2)


def test_validar_b3_em_pregao_inexistente_levanta_not_found(settings, monkeypatch):
    """Em pregão, brapi 404 → SymbolNotFoundError com o símbolo anexado."""
    _congela_relogio(monkeypatch, _ABERTO)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "brapi.dev"
        assert request.url.path == "/api/quote/NOPE4"
        return httpx.Response(404, json={"message": "not found"})

    with _mock_client(handler, requests) as client:
        with pytest.raises(SymbolNotFoundError) as excinfo:
            validar(client, settings, "NOPE4", "b3")
    assert excinfo.value.symbol == "NOPE4"


def test_validar_b3_em_pregao_falha_de_rede_levanta_indisponivel(settings, monkeypatch):
    """Transporte caindo em pregão → SourceUnavailableError (503 na rota, D-04)."""
    _congela_relogio(monkeypatch, _ABERTO)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _mock_client(handler, requests) as client:
        with pytest.raises(SourceUnavailableError) as excinfo:
            validar(client, settings, "PETR4", "b3")
    assert excinfo.value.kind == "unavailable"


def test_validar_b3_fora_do_pregao_nem_consulta(settings, monkeypatch):
    """D-03: fora do pregão valida NADA na brapi — aceito sem rede."""
    _congela_relogio(monkeypatch, _FECHADO)
    requests: list[httpx.Request] = []
    with _mock_client(_violento, requests) as client:
        assert validar(client, settings, "PETR4", "b3") is None
    assert requests == []  # a rede NEM SAIU


def test_erro_nunca_carrega_url_nem_token(settings, monkeypatch):
    """K2/T-04-08: a URL da brapi (com token= na query) nunca vaza na exceção."""
    marcador = "segredo-brapi-nao-vazar"
    com_token = replace(settings, brapi_token=marcador)
    _congela_relogio(monkeypatch, _ABERTO)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _mock_client(handler, requests) as client:
        with pytest.raises(SourceUnavailableError) as excinfo:
            validar(client, com_token, "PETR4", "b3")
    texto = str(excinfo.value)
    assert marcador not in texto
    assert "http" not in texto.lower()
    assert "brapi.dev" not in texto
    assert excinfo.value.__cause__ is None  # from None: sem cadeia de exceção
