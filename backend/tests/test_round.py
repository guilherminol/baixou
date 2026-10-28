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

import json
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from baixou.core.config import load_settings
from baixou.core.db import WORKER_TIMEOUT_S, connect
from baixou.core.migrations import apply_migrations
from baixou.worker import main as worker_main
from baixou.worker.sources.base import b3_market_open

_Handler = Callable[[httpx.Request], httpx.Response]

# Quarta-feira 16:00 UTC = 13:00 em Brasília (UTC-3): dentro do pregão (SC-1).
_MARKET_OPEN_NOW = datetime(2026, 10, 28, 16, 0, 0, tzinfo=UTC)
# Domingo 15:00 UTC = 12:00 em Brasília: fora do pregão (SC-1).
_MARKET_CLOSED_NOW = datetime(2026, 11, 1, 15, 0, 0, tzinfo=UTC)


def _freeze_clock(monkeypatch, now_utc: datetime) -> None:
    """Fixa o ``datetime`` do módulo worker.main num instante arbitrário (determinismo)."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return now_utc.astimezone(tz)
            return now_utc.replace(tzinfo=None)

    monkeypatch.setattr(worker_main, "datetime", _Frozen)


def _freeze_clock_inside_market(monkeypatch) -> None:
    """Congela o relógio dentro do pregão — inerte para a Task 1, define a Task 3."""
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)


@pytest.fixture(autouse=True)
def _reset_cadence_rounds(monkeypatch):
    """Todo teste observa o contador de cadência na fase 0 (ordem de execução irrelevante)."""
    monkeypatch.setattr(worker_main, "_cadence_rounds", 0, raising=False)


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


def test_round_records_both_markets_and_marks_run_ok(db, settings, monkeypatch, fake_http):
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
    _freeze_clock_inside_market(monkeypatch)
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


def test_round_fetches_b3_one_symbol_per_call_serialized(db, settings, monkeypatch, fake_http):
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
    _freeze_clock_inside_market(monkeypatch)
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


# --- Task 3 (SC-1, D-01): pregão da B3 e cadência divisória ---


@pytest.mark.parametrize(
    ("moment_utc", "expected"),
    [
        (datetime(2026, 10, 26, 12, 59, tzinfo=UTC), False),  # segunda 09:59 BRT
        (datetime(2026, 10, 26, 13, 0, tzinfo=UTC), True),  # segunda 10:00 BRT (inclusivo)
        (datetime(2026, 10, 28, 20, 59, tzinfo=UTC), True),  # quarta 17:59 BRT
        (datetime(2026, 10, 30, 21, 0, tzinfo=UTC), False),  # sexta 18:00 BRT (exclusivo)
        (datetime(2026, 10, 31, 15, 0, tzinfo=UTC), False),  # sábado 12:00 BRT
        # Sexta 2018-02-16 na DST histórica (UTC-2): 12:00 UTC = 10:00 local → aberto
        (datetime(2018, 2, 16, 12, 0, tzinfo=UTC), True),
    ],
)
def test_b3_market_open_parametric_table(moment_utc, expected):
    """Tabela de instantes do Pitfall 5 (com DST histórica como saneamento do tzdata)."""
    assert b3_market_open(moment_utc, "America/Sao_Paulo") is expected


def _happy_handlers() -> _Handler:
    """Respostas felizes por host para os dois mercados."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "brapi.dev":
            symbol = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "symbol": symbol,
                            "regularMarketPrice": 38.25,
                            "regularMarketTime": 1793154000,
                            "regularMarketChangePercent": -1.3,
                        }
                    ]
                },
            )
        symbols = json.loads(request.url.params["symbols"])
        return httpx.Response(
            200, json=[{"symbol": symbol, "price": "270000.0"} for symbol in symbols]
        )

    return handler


def test_round_outside_market_hours_skips_b3_but_still_fetches_crypto(
    db, settings, monkeypatch, fake_http
):
    """Fora do pregão (domingo): ZERO requisições à brapi; cripto segue; batimento fica."""
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")
    fake_http.route(_happy_handlers())
    _freeze_clock(monkeypatch, _MARKET_CLOSED_NOW)

    worker_main.run_round(db, settings)

    brapi_requests = [r for r in fake_http.requests if r.url.host == "brapi.dev"]
    assert brapi_requests == []  # nenhum ticker da B3 é consultado (SC-1)

    crypto = db.execute("SELECT * FROM quote WHERE symbol = 'BTCUSDT'").fetchone()
    assert crypto is not None and crypto["price"] == 270000.0  # cripto é o dia todo

    b3_quote = db.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert b3_quote is None  # nada de brapi foi consultado, nada é gravado

    run_row = db.execute("SELECT last_run, worker_lock_at FROM run WHERE id = 1").fetchone()
    assert run_row["last_run"] is not None  # batimento incondicional
    assert run_row["worker_lock_at"] is not None


def test_round_b3_cadence_divisor_consults_only_on_multiples(db, settings, fake_http, monkeypatch):
    """B3_EVERY_N_ROUNDS=3: rodadas 0 e 3 consultam a B3; 1, 2, 4 e 5 não; cripto sempre.

    Rodadas dirigidas consecutivamente dentro de um único teste — o contador é
    interno ao processo e o fixture autouse o observa na fase 0 (D-01).
    """
    cadence_settings = replace(settings, b3_every_n_rounds=3)
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)

    for round_index in range(6):
        worker_main.run_round(db, cadence_settings)
        brapi_so_far = sum(1 for r in fake_http.requests if r.url.host == "brapi.dev")
        expected_b3 = round_index // 3 + 1  # consultas após as rodadas 0..5: 1,1,1,2,2,2
        assert brapi_so_far == expected_b3, f"rodada {round_index}"

    assert sum(1 for r in fake_http.requests if r.url.host == "api.binance.com") == 6


def test_round_with_empty_watch_only_records_heartbeat(db, settings, monkeypatch, fake_http):
    """Watch vazio: nenhuma requisição às fontes e rodada completa com batimento (proibição)."""
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)

    worker_main.run_round(db, settings)

    assert fake_http.requests == []  # rodada vazia continua sendo rodada
    run_row = db.execute("SELECT last_run, worker_lock_at FROM run WHERE id = 1").fetchone()
    assert run_row["last_run"] is not None
    assert run_row["worker_lock_at"] is not None
    assert db.execute("SELECT COUNT(*) AS n FROM source_state").fetchone()["n"] == 0


# --- 02-02 Task 1 (D-03, SC-3): falha tipada de fonte nunca derruba a rodada ---


def test_round_brapi_failure_survives_and_records_source_failure(
    db, settings, monkeypatch, fake_http, capsys
):
    """brapi 500 × binance ok: crypto gravada, falha em source_state, rodada viva (SC-3)."""
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "brapi.dev":
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "270000.0"}])

    fake_http.route(handler)
    _freeze_clock_inside_market(monkeypatch)

    worker_main.run_round(db, settings)  # a rodada NUNCA propaga SourceError

    crypto = db.execute("SELECT * FROM quote WHERE symbol = 'BTCUSDT'").fetchone()
    assert crypto is not None and crypto["price"] == 270000.0  # o outro mercado é gravado

    brapi_state = db.execute("SELECT * FROM source_state WHERE source = 'brapi'").fetchone()
    assert brapi_state["last_attempt_at"] is not None
    assert brapi_state["last_ok_at"] is None  # sem sucesso novo
    assert brapi_state["last_error"] == "unavailable"  # kind do 500
    assert brapi_state["consecutive_failures"] == 1

    binance_state = db.execute("SELECT * FROM source_state WHERE source = 'binance'").fetchone()
    assert binance_state["last_ok_at"] is not None
    assert binance_state["consecutive_failures"] == 0

    run_row = db.execute("SELECT last_run, last_ok FROM run WHERE id = 1").fetchone()
    assert run_row["last_run"] is not None  # batimento incondicional
    assert run_row["last_ok"] is None  # fonte falhou → last_ok não é tocado

    out = capsys.readouterr().out
    assert "unavailable" in out and "PETR4" in out  # evento cita kind e símbolo
    assert "brapi.dev" not in out  # a URL nunca entra no log (K2)

    # Segunda rodada ainda falhando: consecutive_failures acumula (insumo do ALERT-03).
    worker_main.run_round(db, settings)
    brapi_state = db.execute("SELECT * FROM source_state WHERE source = 'brapi'").fetchone()
    assert brapi_state["consecutive_failures"] == 2


def test_round_brapi_not_found_marks_item_and_recovers_next_round(
    db, settings, monkeypatch, fake_http
):
    """2 tickers, 1 com 404: o ok é gravado, o falhado recebe last_fetch_error='not_found';
    na rodada seguinte com sucesso a coluna volta a NULL (D-03)."""
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "VALE3", "b3")

    vale3_calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        symbol = request.url.path.rsplit("/", 1)[-1]
        if symbol == "VALE3":
            vale3_calls["count"] += 1
            if vale3_calls["count"] == 1:  # só a primeira rodada falha
                return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "symbol": symbol,
                        "regularMarketPrice": 38.25,
                        "regularMarketTime": 1793154000,
                        "regularMarketChangePercent": -1.3,
                    }
                ]
            },
        )

    fake_http.route(handler)
    _freeze_clock_inside_market(monkeypatch)

    worker_main.run_round(db, settings)

    petr4 = db.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert petr4 is not None and petr4["price"] == 38.25  # o ok é gravado
    assert petr4["last_fetch_error"] is None

    vale3 = db.execute("SELECT * FROM quote WHERE symbol = 'VALE3'").fetchone()
    assert vale3 is not None and vale3["last_fetch_error"] == "not_found"  # erro por item

    brapi_state = db.execute("SELECT * FROM source_state WHERE source = 'brapi'").fetchone()
    assert brapi_state["last_ok_at"] is not None  # not_found parcial não é falha de fonte
    assert brapi_state["consecutive_failures"] == 0

    # Rodada seguinte com o ticker ok de novo: a coluna dele volta a NULL.
    worker_main.run_round(db, settings)
    vale3 = db.execute("SELECT * FROM quote WHERE symbol = 'VALE3'").fetchone()
    assert vale3["price"] == 38.25
    assert vale3["last_fetch_error"] is None


def test_round_error_log_never_leaks_brapi_token(tmp_path, monkeypatch, fake_http, capsys):
    """K2/T-02-04: com BRAPI_TOKEN na env, a falha logada não contém o valor nem 'token='."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ_MARKET", "America/Sao_Paulo")
    monkeypatch.setenv("POLL_MINUTES", "15")
    monkeypatch.setenv("LOG_LEVEL", "info")
    monkeypatch.setenv("BRAPI_TOKEN", "tok-secreto-xyz")
    settings = load_settings()
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        seed_watch(conn, "PETR4", "b3")
        seed_watch(conn, "BTCUSDT", "crypto")

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "brapi.dev":
                return httpx.Response(401, json={"message": "invalid token"})
            return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "270000.0"}])

        fake_http.route(handler)
        _freeze_clock_inside_market(monkeypatch)
        worker_main.run_round(conn, settings)

        captured = capsys.readouterr()
        whole_output = captured.out + captured.err
        assert "tok-secreto-xyz" not in whole_output  # o valor nunca aparece
        assert "token=" not in whole_output  # nem o trecho da query (K2)
        assert "auth" in whole_output  # prova de que a falha foi logada (evento com o kind)
    finally:
        conn.close()
