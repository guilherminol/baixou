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
from baixou.worker import quota
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


def _freeze_quota_month(monkeypatch, now_utc: datetime) -> None:
    """Congela o mês UTC visto pelo módulo de cota (mesmo mecanismo do worker.main)."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return now_utc.astimezone(tz)
            return now_utc.replace(tzinfo=None)

    monkeypatch.setattr(quota, "datetime", _Frozen)


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


def seed_watch(conn: sqlite3.Connection, symbol: str, market: str, paused: int = 0) -> None:
    """Semeia um ticker na watch (isolation_level=None: INSERT em autocommit).

    ``paused`` semeia a coluna da migração 0004 (04-03): default 0 = ativo.
    """
    conn.execute(
        "INSERT INTO watch (symbol, market, target_below, paused) VALUES (?, ?, ?, ?)",
        (symbol, market, 10.0, paused),
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


# --- 02-02 Task 2 (D-02, SC-2): quarentena do símbolo inválido do lote Binance ---


def test_round_quarantines_bad_binance_symbol_until_watch_row_is_recreated(
    db, settings, monkeypatch, fake_http
):
    """400 -1121: 1-a-1 identifica o ruim, os válidos são gravados mesmo na rodada do
    400 (SC-2); o ruim sai do lote das rodadas seguintes e volta ao recriar a watch."""
    seed_watch(db, "BTCUSDT", "crypto")
    seed_watch(db, "ETHUSDT", "crypto")

    batches: list[list[str]] = []
    singles: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "symbols" in request.url.params:
            symbols = json.loads(request.url.params["symbols"])
            batches.append(symbols)
            if "ETHUSDT" in symbols:  # o lote é all-or-nothing com o ruim dentro
                return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
            return httpx.Response(200, json=[{"symbol": s, "price": "270000.0"} for s in symbols])
        symbol = request.url.params["symbol"]
        singles.append(symbol)
        if symbol == "ETHUSDT":  # o ruim também falha sozinho (400 -1121)
            return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
        return httpx.Response(200, json={"symbol": symbol, "price": "270000.0"})

    fake_http.route(handler)
    _freeze_clock_inside_market(monkeypatch)

    # Rodada 1: o 400 dispara o 1-a-1 UMA vez; o ruim é identificado e gravado.
    worker_main.run_round(db, settings)
    assert batches == [["BTCUSDT", "ETHUSDT"]]  # 1 lote com os dois
    assert singles == ["BTCUSDT", "ETHUSDT"]  # 1-a-1: cada símbolo uma vez

    btc = db.execute("SELECT * FROM quote WHERE symbol = 'BTCUSDT'").fetchone()
    assert btc is not None and btc["price"] == 270000.0  # SC-2: o válido é gravado
    eth = db.execute("SELECT * FROM quote WHERE symbol = 'ETHUSDT'").fetchone()
    assert eth is not None and eth["last_fetch_error"] == "not_found"  # erro por item

    binance_state = db.execute("SELECT * FROM source_state WHERE source = 'binance'").fetchone()
    assert binance_state["last_ok_at"] is not None  # not_found parcial não é falha de fonte
    assert binance_state["consecutive_failures"] == 0

    # Rodada 2: o símbolo em quarentena NÃO entra no lote (peso 4, não N singles).
    worker_main.run_round(db, settings)
    assert batches[1] == ["BTCUSDT"]  # o lote único segue com os válidos
    assert singles == ["BTCUSDT", "ETHUSDT"]  # nenhuma chamada 1-a-1 nova

    btc = db.execute("SELECT * FROM quote WHERE symbol = 'BTCUSDT'").fetchone()
    assert btc["price"] == 270000.0  # o válido continua gravado em toda rodada

    # Após a migração 0004 (D-01): remover a linha em watch NÃO apaga mais a
    # quote — a órfã sobrevive COM o last_fetch_error (o pruning do worker,
    # Task 3 deste plano, é quem a apaga na rodada seguinte).
    db.execute("DELETE FROM watch WHERE symbol = 'ETHUSDT' AND market = 'crypto'")
    orfa = db.execute("SELECT * FROM quote WHERE symbol = 'ETHUSDT'").fetchone()
    assert orfa is not None and orfa["last_fetch_error"] == "not_found"  # órfã sobrevive (D-01)

    # Recriada a watch com a órfã ainda no banco (o pruning não rodou), a
    # quarentena DERIVADA da coluna continua valendo: o símbolo não vai ao lote.
    db.execute(
        "INSERT INTO watch (symbol, market, target_below) VALUES ('ETHUSDT', 'crypto', 10.0)"
    )
    worker_main.run_round(db, settings)
    assert batches[2] == ["BTCUSDT"]  # a órfã existe → quarentena mantida

    # O reset da recriação pela API (D-02, _RESET_QUOTE do POST/PUT do 04-02)
    # é quem limpa a coluna — simulado aqui pela mesma escrita:
    db.execute("UPDATE quote SET last_fetch_error = NULL WHERE symbol = 'ETHUSDT'")
    worker_main.run_round(db, settings)
    assert batches[3] == ["BTCUSDT", "ETHUSDT"]  # sem quarentena: de volta ao lote único
    # 1-a-1 de novo: o lote recusado repete TODOS os símbolos (ordem da watch)
    assert singles == ["BTCUSDT", "ETHUSDT", "BTCUSDT", "ETHUSDT"]


# --- 02-02 Task 3 (D-04, SC-2): espelho 451 na rodada ---


def test_round_451_fails_over_to_mirror_and_counts_as_success(
    db, settings, monkeypatch, fake_http, capsys
):
    """451 no principal: o espelho assume, a cotação é gravada e CONTA como sucesso."""
    seed_watch(db, "BTCUSDT", "crypto")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.binance.com":
            return httpx.Response(451)
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "270000.0"}])

    fake_http.route(handler)
    _freeze_clock_inside_market(monkeypatch)

    worker_main.run_round(db, settings)

    hosts = {r.url.host for r in fake_http.requests}
    assert hosts == {"api.binance.com", "data-api.binance.vision"}  # a troca aconteceu

    crypto = db.execute("SELECT * FROM quote WHERE symbol = 'BTCUSDT'").fetchone()
    assert crypto is not None and crypto["price"] == 270000.0

    binance_state = db.execute("SELECT * FROM source_state WHERE source = 'binance'").fetchone()
    assert binance_state["last_ok_at"] is not None  # espelho conta como sucesso
    assert binance_state["consecutive_failures"] == 0
    assert binance_state["last_error"] is None

    run_row = db.execute("SELECT last_run, last_ok FROM run WHERE id = 1").fetchone()
    assert run_row["last_ok"] is not None  # nenhuma fonte falhou

    out = capsys.readouterr().out
    assert out.count("mirror_fallback") == 1  # evento único, não por rodada


def test_round_binance_blocked_on_both_hosts_round_survives(
    db, settings, monkeypatch, fake_http, capsys
):
    """451 nos dois endereços: SourceUnavailableError na rodada, que sobrevive (SC-3)."""
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")

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
        return httpx.Response(451)  # principal E espelho bloqueados

    fake_http.route(handler)
    _freeze_clock_inside_market(monkeypatch)

    worker_main.run_round(db, settings)  # a rodada NUNCA levanta

    petr4 = db.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert petr4 is not None and petr4["price"] == 38.25  # a B3 não é silenciada

    binance_state = db.execute("SELECT * FROM source_state WHERE source = 'binance'").fetchone()
    assert binance_state["last_ok_at"] is None
    assert binance_state["last_error"] == "unavailable"
    assert binance_state["consecutive_failures"] == 1

    run_row = db.execute("SELECT last_run, last_ok FROM run WHERE id = 1").fetchone()
    assert run_row["last_run"] is not None  # batimento incondicional
    assert run_row["last_ok"] is None  # uma fonte falhou

    out = capsys.readouterr().out
    assert out.count("mirror_fallback") == 1  # UMA troca (principal → espelho), não duas
    assert "unavailable" in out  # a falha do lote é registrada com o kind


def test_round_two_crypto_symbols_fetch_in_one_compact_batch(db, settings, monkeypatch, fake_http):
    """G-03-3: rodada com DOIS itens cripto busca TODOS no lote único (POLL-02).

    O handler reproduz o modo de falha REAL provado ao vivo (03-UAT.md):
    parâmetro ``symbols`` com espaço após a vírgula → a Binance recusa o
    lote com HTTP 400 code -1100 → kind=unexpected e a RODADA INTEIRA perde
    a cotação cripto (consecutive_failures acumulando, nenhum preço gravado).
    Compacto, o mesmo lote volta 200 com os dois preços.
    """
    seed_watch(db, "BTCUSDT", "crypto")
    seed_watch(db, "ETHUSDT", "crypto")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("symbols") != '["BTCUSDT","ETHUSDT"]':
            return httpx.Response(
                400,
                json={
                    "code": -1100,
                    "msg": "Illegal characters found in parameter; 'symbols'.",
                },
            )
        return httpx.Response(
            200,
            json=[
                {"symbol": "BTCUSDT", "price": "270000.0"},
                {"symbol": "ETHUSDT", "price": "1800.0"},
            ],
        )

    fake_http.route(handler)
    _freeze_clock_inside_market(monkeypatch)
    worker_main.run_round(db, settings)

    binance_requests = [r for r in fake_http.requests if r.url.host == "api.binance.com"]
    assert len(binance_requests) == 1  # lote único: UMA requisição de peso 4 (nunca 1-a-1)
    assert binance_requests[0].url.params["symbols"] == '["BTCUSDT","ETHUSDT"]'  # a CRUA

    for symbol, price in (("BTCUSDT", 270000.0), ("ETHUSDT", 1800.0)):
        row = db.execute("SELECT * FROM quote WHERE symbol = ?", (symbol,)).fetchone()
        assert row is not None and row["price"] == price  # preço gravado para AMBOS

    binance_state = db.execute("SELECT * FROM source_state WHERE source = 'binance'").fetchone()
    assert binance_state["consecutive_failures"] == 0
    assert binance_state["last_ok_at"] is not None


# --- 02-03 Task 1 (D-05, D-06): contagem persistida da cota brapi ---


def test_round_counts_brapi_calls_across_rounds_and_ignores_crypto(
    db, settings, monkeypatch, fake_http
):
    """2 tickers brapi × 2 rodadas → calls=4; cripto (lote Binance) não soma."""
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "VALE3", "b3")
    seed_watch(db, "BTCUSDT", "crypto")
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)
    _freeze_quota_month(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(db, settings)
    worker_main.run_round(db, settings)

    row = db.execute("SELECT * FROM quota WHERE source = 'brapi'").fetchone()
    assert row is not None
    assert row["calls"] == 4  # só os fetch_one executados da brapi contam
    assert row["month"] == "2026-10"  # mês UTC do relógio congelado (D-06)
    assert row["warned_80"] == 0


def test_round_outside_market_hours_does_not_count_quota(db, settings, monkeypatch, fake_http):
    """Rodada com pregão fechado: nenhuma chamada brapi → nada entra no contador."""
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")
    fake_http.route(_happy_handlers())
    _freeze_clock(monkeypatch, _MARKET_CLOSED_NOW)
    _freeze_quota_month(monkeypatch, _MARKET_CLOSED_NOW)

    worker_main.run_round(db, settings)

    # Sem chamada brapi e sem virada a processar: a linha nem nasce.
    assert db.execute("SELECT COUNT(*) AS n FROM quota").fetchone()["n"] == 0

    # Chamadas de rodadas anteriores permanecem e a rodada fechada NÃO soma por cima.
    db.execute(
        "INSERT INTO quota (source, month, calls, warned_80) VALUES ('brapi', '2026-11', 4, 0)"
    )
    worker_main.run_round(db, settings)
    row = db.execute("SELECT * FROM quota WHERE source = 'brapi'").fetchone()
    assert row["calls"] == 4  # inalterado


def test_round_quota_warning_80_fires_once(db, settings, monkeypatch, fake_http, capsys):
    """limit=10 e 2 tickers brapi: o aviso sai exatamente na rodada que cruza 8, uma vez."""
    limit10 = replace(settings, brapi_monthly_limit=10)
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "VALE3", "b3")
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)
    _freeze_quota_month(monkeypatch, _MARKET_OPEN_NOW)

    for _ in range(3):  # rodadas 1–3: calls 2, 4, 6 — nenhuma cruza
        worker_main.run_round(db, limit10)
    assert "quota_warning_80" not in capsys.readouterr().out

    worker_main.run_round(db, limit10)  # rodada 4: calls=8 → cruza 0.8×10
    assert capsys.readouterr().out.count("quota_warning_80") == 1

    worker_main.run_round(db, limit10)  # rodada 5: warned_80=1 impede repetição
    assert "quota_warning_80" not in capsys.readouterr().out

    row = db.execute("SELECT * FROM quota WHERE source = 'brapi'").fetchone()
    assert row["calls"] == 10
    assert row["warned_80"] == 1


# --- 02-03 Task 2 (D-05, SC-4): parada da B3 a 100% com cripto seguindo ---


def test_round_quota_exhausted_blocks_b3_but_crypto_continues(
    db, settings, monkeypatch, fake_http, capsys
):
    """Porta fechada (calls >= limite): ZERO requisições brapi; cripto e batimento seguem."""
    limit10 = replace(settings, brapi_monthly_limit=10)
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")
    # Cota já esgotada em rodadas anteriores (a transição já foi emitida e contada):
    db.execute(
        "INSERT INTO quota (source, month, calls, warned_80) VALUES ('brapi', '2026-10', 10, 1)"
    )
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)
    _freeze_quota_month(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(db, limit10)
    worker_main.run_round(db, limit10)  # rodada seguinte com a porta fechada

    assert [r for r in fake_http.requests if r.url.host == "brapi.dev"] == []  # T-02-09
    assert [r for r in fake_http.requests if r.url.host == "api.binance.com"]  # cripto segue

    crypto = db.execute("SELECT * FROM quote WHERE symbol = 'BTCUSDT'").fetchone()
    assert crypto is not None and crypto["price"] == 270000.0  # cripto gravada

    assert (
        db.execute("SELECT * FROM source_state WHERE source = 'brapi'").fetchone() is None
    )  # bloqueada não é tentativa: nada em source_state

    row = db.execute("SELECT * FROM quota WHERE source = 'brapi'").fetchone()
    assert row["calls"] == 10  # chamada bloqueada não soma
    assert row["warned_80"] == 1

    run_row = db.execute(
        "SELECT last_run, last_ok, worker_lock_at FROM run WHERE id = 1"
    ).fetchone()
    assert run_row["last_run"] is not None  # batimento incondicional
    assert run_row["worker_lock_at"] is not None
    assert run_row["last_ok"] is not None  # nenhuma fonte consultada falhou
    assert capsys.readouterr().out.count("quota_exhausted") == 0  # transição não repete


def test_round_last_permitted_call_executes_and_counts(
    db, settings, monkeypatch, fake_http, capsys
):
    """Gravado 9 de 10: a ÚLTIMA chamada permitida executa e conta; a seguinte é bloqueada."""
    limit10 = replace(settings, brapi_monthly_limit=10)
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "VALE3", "b3")
    db.execute(
        "INSERT INTO quota (source, month, calls, warned_80) VALUES ('brapi', '2026-10', 9, 1)"
    )
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)
    _freeze_quota_month(monkeypatch, _MARKET_OPEN_NOW)

    def brapi_symbols_so_far():
        return [
            r.url.path.rsplit("/", 1)[-1] for r in fake_http.requests if r.url.host == "brapi.dev"
        ]

    worker_main.run_round(db, limit10)

    assert brapi_symbols_so_far() == ["PETR4"]  # a última permitida saiu; VALE3 é bloqueada
    row = db.execute("SELECT * FROM quota WHERE source = 'brapi'").fetchone()
    assert row["calls"] == 10  # a chamada que cruzou executou E contou
    assert capsys.readouterr().out.count("quota_exhausted") == 1  # transição única

    vale3 = db.execute("SELECT * FROM quote WHERE symbol = 'VALE3'").fetchone()
    assert vale3 is None  # bloqueada: sem requisição, sem linha, sem erro por item
    brapi_state = db.execute("SELECT * FROM source_state WHERE source = 'brapi'").fetchone()
    assert brapi_state is not None and brapi_state["last_ok_at"] is not None  # a saída foi ok

    # Rodada seguinte: porta fechada, nenhuma chamada nova, nenhum evento repetido.
    worker_main.run_round(db, limit10)
    assert brapi_symbols_so_far() == ["PETR4"]
    assert capsys.readouterr().out.count("quota_exhausted") == 0


def test_round_brapi_429_does_not_close_quota_door(db, settings, monkeypatch, fake_http, capsys):
    """429 com calls < limite é RateLimitError em source_state (02-02) e NÃO fecha a porta."""
    limit10 = replace(settings, brapi_monthly_limit=10)
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")
    rate_limited = {"on": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "brapi.dev":
            if rate_limited["on"]:
                return httpx.Response(429)
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
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "270000.0"}])

    fake_http.route(handler)
    _freeze_clock_inside_market(monkeypatch)
    _freeze_quota_month(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(db, limit10)  # rodada 1: 429 — a chamada executou e CONTA
    brapi_state = db.execute("SELECT * FROM source_state WHERE source = 'brapi'").fetchone()
    assert brapi_state["last_error"] == "rate_limit"  # falha tipada do 02-02
    assert brapi_state["consecutive_failures"] == 1
    row = db.execute("SELECT * FROM quota WHERE source = 'brapi'").fetchone()
    assert row["calls"] == 1  # chamada executada soma mesmo com 429
    assert "quota_exhausted" not in capsys.readouterr().out  # 429 não é esgotamento de cota

    rate_limited["on"] = False
    worker_main.run_round(db, limit10)  # rodada 2: porta aberta (1 < 10) → nova chamada sai
    brapi_requests = [r for r in fake_http.requests if r.url.host == "brapi.dev"]
    assert len(brapi_requests) == 2  # a porta NÃO fechou com o 429 (fonte ≠ cota)
    brapi_state = db.execute("SELECT * FROM source_state WHERE source = 'brapi'").fetchone()
    assert brapi_state["consecutive_failures"] == 0  # sucesso zera (02-02)


def test_round_quota_door_reopens_after_utc_rollover(db, settings, monkeypatch, fake_http):
    """Porta fechada em outubro; na primeira rodada do mês novo UTC a B3 volta a ser consultada."""
    limit10 = replace(settings, brapi_monthly_limit=10)
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")
    db.execute(
        "INSERT INTO quota (source, month, calls, warned_80) VALUES ('brapi', '2026-10', 10, 1)"
    )
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)
    _freeze_quota_month(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(db, limit10)
    assert [r for r in fake_http.requests if r.url.host == "brapi.dev"] == []  # fechada

    _freeze_quota_month(monkeypatch, datetime(2026, 11, 1, 13, 0, tzinfo=UTC))  # mês novo UTC
    worker_main.run_round(db, limit10)

    brapi_requests = [r for r in fake_http.requests if r.url.host == "brapi.dev"]
    assert len(brapi_requests) == 1  # a virada destrava já na primeira rodada do mês novo
    row = db.execute("SELECT * FROM quota WHERE source = 'brapi'").fetchone()
    assert row["month"] == "2026-11"  # reset da virada (D-06)
    assert row["calls"] == 1  # e a chamada da rodada nova conta a partir do zero
    assert row["warned_80"] == 0


# --- 04-03 Task 3 (D-09/D-01): pausa obedecida ANTES da fonte e pruning de órfãs ---


def test_round_skips_paused_item_never_consulting_its_source(db, settings, monkeypatch, fake_http):
    """PETR4 pausado × BTCUSDT ativo: ZERO requisições brapi; exatamente a da Binance (D-09)."""
    seed_watch(db, "PETR4", "b3", paused=1)
    seed_watch(db, "BTCUSDT", "crypto")
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)

    worker_main.run_round(db, settings)

    brapi_requests = [r for r in fake_http.requests if r.url.host == "brapi.dev"]
    assert brapi_requests == []  # a cota da brapi não é queimada pelo pausado
    binance_requests = [r for r in fake_http.requests if r.url.host == "api.binance.com"]
    assert len(binance_requests) == 1  # o ativo segue com o lote único normal
    assert json.loads(binance_requests[0].url.params["symbols"]) == ["BTCUSDT"]


def test_round_keeps_paused_quote_frozen(db, settings, monkeypatch, fake_http):
    """Quote do pausado bit-a-bit igual antes/depois da rodada: sem linha nova, sem erro novo."""
    seed_watch(db, "PETR4", "b3", paused=1)
    db.execute(
        "INSERT INTO quote (symbol, market, price, price_at, change_pct, alerted) "
        "VALUES ('PETR4', 'b3', 35.2, '2026-10-28T15:45:00Z', -1.2, 0)"
    )
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)

    _SELECAO = "SELECT price, price_at, change_pct, alerted, last_fetch_error FROM quote"
    antes = tuple(db.execute(f"{_SELECAO} WHERE symbol = 'PETR4'").fetchone())
    worker_main.run_round(db, settings)
    depois = tuple(db.execute(f"{_SELECAO} WHERE symbol = 'PETR4'").fetchone())

    assert antes == depois  # congelada: nenhum campo mudou
    assert (
        db.execute("SELECT COUNT(*) AS n FROM quote WHERE market = 'b3'").fetchone()["n"] == 1
    )  # sem linha nova
    run_row = db.execute("SELECT last_run FROM run WHERE id = 1").fetchone()
    assert run_row["last_run"] is not None  # a rodada segue completa com o batimento


def test_round_resume_consults_source_again_next_round(db, settings, monkeypatch, fake_http):
    """Retomar (paused=0): a rodada seguinte reconsulta a fonte — o frescor se resolve sozinho."""
    seed_watch(db, "PETR4", "b3", paused=1)
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)

    worker_main.run_round(db, settings)
    assert [r for r in fake_http.requests if r.url.host == "brapi.dev"] == []  # pausado não sai

    db.execute("UPDATE watch SET paused = 0 WHERE symbol = 'PETR4'")
    worker_main.run_round(db, settings)

    brapi_requests = [r for r in fake_http.requests if r.url.host == "brapi.dev"]
    assert [r.url.path.rsplit("/", 1)[-1] for r in brapi_requests] == ["PETR4"]  # reconsultado
    quote = db.execute("SELECT price FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["price"] == 38.25  # frescor resolvido na própria rodada


def test_round_prunes_orphan_quotes_even_with_fetch_error(db, settings, monkeypatch, fake_http):
    """Órfã com last_fetch_error='not_found' é apagada pela rodada; presentes permanecem (D-01)."""
    seed_watch(db, "PETR4", "b3")
    seed_watch(db, "BTCUSDT", "crypto")
    # Órfã direto no banco (o pai já saiu pela web): a migração 0004 deixou de
    # cascata — ela só some pelo pruning da write_tx única.
    db.execute(
        "INSERT INTO quote (symbol, market, price, alerted, last_fetch_error) "
        "VALUES ('VALE3', 'b3', 61.0, 1, 'not_found')"
    )
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)

    worker_main.run_round(db, settings)

    orfas = db.execute("SELECT COUNT(*) AS n FROM quote WHERE symbol = 'VALE3'").fetchone()["n"]
    assert orfas == 0  # a órfã some, inclusive com o last_fetch_error preenchido
    assert db.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"] == 2  # watch intacta
    assert db.execute("SELECT price FROM quote WHERE symbol = 'PETR4'").fetchone()["price"] == 38.25
    assert (
        db.execute("SELECT price FROM quote WHERE symbol = 'BTCUSDT'").fetchone()["price"]
        == 270000.0
    )  # as quotes dos presentes permanecem


def test_round_with_empty_watch_still_prunes_orphans(db, settings, monkeypatch, fake_http):
    """Rodada sem nenhum item em watch ainda poda órfãs (DELETE incondicional na write_tx)."""
    db.execute(
        "INSERT INTO quote (symbol, market, price, last_fetch_error) "
        "VALUES ('SOLUSDT', 'crypto', 96.5, NULL)"
    )
    fake_http.route(_happy_handlers())
    _freeze_clock_inside_market(monkeypatch)

    worker_main.run_round(db, settings)

    assert db.execute("SELECT COUNT(*) AS n FROM quote").fetchone()["n"] == 0  # órfã podada
    run_row = db.execute("SELECT last_run, worker_lock_at FROM run WHERE id = 1").fetchone()
    assert run_row["last_run"] is not None  # a rodada segue completa
    assert run_row["worker_lock_at"] is not None
