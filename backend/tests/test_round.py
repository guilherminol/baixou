"""Rodada real com fontes falsas — ponta a ponta sem rede (POLL-01, POLL-02).

``run_round(conn, settings)`` exercitada com transportes httpx falsos
injetados (``MockTransport`` roteado por host): gravação de quote dos dois
mercados, source_state ok das duas fontes, ``run.last_ok`` e serialização da
brapi. As consultas acontecem FORA de transação e a gravação em transação
única — o árbitro é o teste passar com as escritas dentro da transação única.
Pregão e cadência (Task 3) e transmissão do token (Task 2) entram nos casos
próprios; aqui a rodada consulta todos os tickers (stub consciente da Task 1).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
from baixou.core.config import load_settings
from baixou.core.db import WORKER_TIMEOUT_S, connect
from baixou.core.migrations import apply_migrations
from baixou.worker import main as worker_main

_Handler = Callable[[httpx.Request], httpx.Response]

# Quarta-feira 16:00 UTC = 13:00 em Brasília (UTC-3): dentro do pregão (SC-1).
_MARKET_OPEN_NOW = datetime(2026, 10, 28, 16, 0, 0, tzinfo=UTC)


def _freeze_clock_inside_market(monkeypatch) -> None:
    """Fixa o ``datetime`` do módulo worker.main num instante dentro do pregão.

    Mesmo mecanismo dos testes de pregão: determinismo total da rodada — o
    congelamento é inerte para os casos que consultam todos os tickers e
    garante que a chamada brapi continue acontecendo depois de o filtro de
    pregão da Task 3 existir.
    """

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return _MARKET_OPEN_NOW.astimezone(tz)
            return _MARKET_OPEN_NOW.replace(tzinfo=None)

    monkeypatch.setattr(worker_main, "datetime", _Frozen)


class _FakeHttp:
    """Recorder do transporte falso: requisições por chamada + rota definida no teste."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._handler: _Handler | None = None

    def route(self, handler: _Handler) -> None:
        self._handler = handler

    def dispatch(self, request: httpx.Request) -> httpx.Response:
        if self._handler is None:
            raise AssertionError("nenhuma rota definida para o transporte falso")
        self.requests.append(request)
        return self._handler(request)


@pytest.fixture
def fake_http(monkeypatch) -> _FakeHttp:
    """Injeta um MockTransport no httpx.Client usado pela rodada (sem rede)."""
    fake = _FakeHttp()
    real_client = httpx.Client

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(fake.dispatch)
        return real_client(**kwargs)

    monkeypatch.setattr(worker_main.httpx, "Client", client_factory)
    return fake


def seed_watch(conn: sqlite3.Connection, symbol: str, market: str) -> None:
    """Semeia um ticker na watch (isolation_level=None: INSERT em autocommit)."""
    conn.execute(
        "INSERT INTO watch (symbol, market, target_below) VALUES (?, ?, ?)",
        (symbol, market, 10.0),
    )


def test_round_records_both_markets_and_marks_run_ok(db, settings, fake_http):
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "brapi.dev":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "symbol": "PETR4",
                            "regularMarketPrice": 38.25,
                            "regularMarketTime": 1793154000,
                            "regularMarketChangePercent": -1.3,
                        }
                    ]
                },
            )
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "270000.0"}])

    fake_http.route(handler)
    worker_main.run_round(db, settings)

    requests = fake_http.requests
    assert {request.url.host for request in requests} == {"brapi.dev", "api.binance.com"}

    quote = db.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["market"] == "b3"
    assert quote["price"] == 38.25
    assert quote["change_pct"] == -1.3
    assert quote["price_at"] == "2026-10-28T02:20:00Z"  # epoch 1793154000 → UTC ISO

    crypto = db.execute("SELECT * FROM quote WHERE symbol = 'BTCUSDT'").fetchone()
    assert crypto["market"] == "crypto"
    assert crypto["price"] == 270000.0
    assert crypto["price_at"] is not None

    for source in ("brapi", "binance"):
        state = db.execute("SELECT * FROM source_state WHERE source = ?", (source,)).fetchone()
        assert state["last_attempt_at"] is not None
        assert state["last_ok_at"] is not None
        assert state["consecutive_failures"] == 0
        assert state["last_error"] is None

    run_row = db.execute(
        "SELECT last_run, last_ok, worker_lock_at FROM run WHERE id = 1"
    ).fetchone()
    assert run_row["last_run"] is not None
    assert run_row["last_ok"] is not None  # nenhuma fonte falhou
    assert run_row["worker_lock_at"] is not None


def test_round_fetches_b3_one_symbol_per_call_serialized(db, settings, fake_http):
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "VALE3", "b3")

    prices = {"PETR4": 38.25, "VALE3": 61.0}

    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "symbol": symbol,
                        "regularMarketPrice": prices[symbol],
                        "regularMarketTime": 1793154000,
                        "regularMarketChangePercent": 0.5,
                    }
                ]
            },
        )

    fake_http.route(handler)
    worker_main.run_round(db, settings)

    brapi_requests = [r for r in fake_http.requests if r.url.host == "brapi.dev"]
    assert len(brapi_requests) == 2  # um símbolo por chamada, em sequência
    assert {r.url.path.rsplit("/", 1)[-1] for r in brapi_requests} == {"PETR4", "VALE3"}
    stored = {
        row["symbol"]: row["price"]
        for row in db.execute("SELECT symbol, price FROM quote WHERE market = 'b3'")
    }
    assert stored == prices


def test_round_transmits_configured_brapi_token(tmp_path, monkeypatch, fake_http):
    """Config validada é consumida pela fonte (Task 2): BRAPI_TOKEN chega à brapi.

    Settings construído via ``load_settings(environ)`` com ``BRAPI_TOKEN``
    definido; a requisição capturada pelo MockTransport carrega o valor
    configurado no query param — prova de que a config não é morta. O relógio
    fica fixado dentro do pregão (mesmo mecanismo da Task 3).
    """
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ_MARKET", "America/Sao_Paulo")
    monkeypatch.setenv("POLL_MINUTES", "15")
    monkeypatch.setenv("LOG_LEVEL", "info")
    monkeypatch.setenv("BRAPI_TOKEN", "tok-da-env-123")
    settings = load_settings()
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        seed_watch(conn, "PETR4", "b3")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "symbol": "PETR4",
                            "regularMarketPrice": 38.25,
                            "regularMarketTime": 1793154000,
                            "regularMarketChangePercent": -1.3,
                        }
                    ]
                },
            )

        fake_http.route(handler)
        _freeze_clock_inside_market(monkeypatch)
        worker_main.run_round(conn, settings)

        brapi_request = next(r for r in fake_http.requests if r.url.host == "brapi.dev")
        assert dict(brapi_request.url.params).get("token") == "tok-da-env-123"
    finally:
        conn.close()
