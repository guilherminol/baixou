"""Rodada com o alerta de ponta a ponta — cruzamento para baixo até os canais simulados.

Mesmos padrões de test_round.py (``MockTransport`` roteado por host, relógio
congelado, banco recém-migrado), com o canal Telegram configurado: brapi
responde abaixo do alvo, o Telegram simulado aceita e o item fica alertado com
alerted/alerted_at/alerted_target/notify_state no formato final (SC-1, D-12,
D-15). Rodada repetida com o preço ainda abaixo não reenvia — um aviso por
cruzamento; canal recusado grava a falha por canal e o item segue aguardando
(SC-3 — a rodada seguinte tenta de novo); um canal aceitando e outro recusando
alerta com o resultado por canal gravado.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest
from baixou.core.config import Settings, load_settings
from baixou.core.db import WORKER_TIMEOUT_S, connect
from baixou.core.migrations import apply_migrations
from baixou.worker import main as worker_main

_Handler = Callable[[httpx.Request], httpx.Response]

# Quarta-feira 16:00 UTC = 13:00 em Brasília: dentro do pregão (SC-1).
_MARKET_OPEN_NOW = datetime(2026, 10, 28, 16, 0, 0, tzinfo=UTC)

_TELEGRAM_TOKEN = "123456789:bot-tok-secreto-xyz"  # formato <id>:<hash> exigido na subida (D-09)
_TELEGRAM_CHAT_ID = "42"
_NTFY_TOPIC = "baixou-e2e"


def _freeze_clock(monkeypatch, now_utc: datetime) -> None:
    """Fixa o ``datetime`` do módulo worker.main num instante arbitrário (determinismo)."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return now_utc.astimezone(tz)
            return now_utc.replace(tzinfo=None)

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


@pytest.fixture
def alert_env(tmp_path, monkeypatch) -> tuple[Settings, sqlite3.Connection]:
    """Settings com o canal Telegram configurado + banco recém-migrado."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ_MARKET", "America/Sao_Paulo")
    monkeypatch.setenv("POLL_MINUTES", "15")
    monkeypatch.setenv("LOG_LEVEL", "info")
    monkeypatch.setenv("TELEGRAM_TOKEN", _TELEGRAM_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", _TELEGRAM_CHAT_ID)
    settings = load_settings()
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    yield settings, conn
    conn.close()


@pytest.fixture
def alert_env_two_channels(tmp_path, monkeypatch) -> tuple[Settings, sqlite3.Connection]:
    """Settings com Telegram E ntfy configurados + banco recém-migrado (SC-3)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ_MARKET", "America/Sao_Paulo")
    monkeypatch.setenv("POLL_MINUTES", "15")
    monkeypatch.setenv("LOG_LEVEL", "info")
    monkeypatch.setenv("TELEGRAM_TOKEN", _TELEGRAM_TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", _TELEGRAM_CHAT_ID)
    monkeypatch.setenv("NTFY_TOPIC", _NTFY_TOPIC)
    settings = load_settings()
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    yield settings, conn
    conn.close()


def _seed_petr4(conn: sqlite3.Connection, target: float = 10.0) -> None:
    """Semeia PETR4 na watch com o alvo do teste (isolation_level=None: autocommit)."""
    conn.execute(
        "INSERT INTO watch (symbol, market, target_below) VALUES ('PETR4', 'b3', ?)",
        (target,),
    )


def _brapi_price(price: float) -> _Handler:
    """Handler que responde o preço na brapi e aceita o POST do Telegram.

    ``regularMarketTime`` 1793203200 = 2026-10-28T16:00:00Z — cotação FRESCA
    em relação ao relógio congelado (o gate de frescor por idade, D-11, não
    avalia cotação velha; idade 0/15 min fica dentro do limite da B3, 75 min).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "brapi.dev":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "symbol": "PETR4",
                            "regularMarketPrice": price,
                            "regularMarketTime": 1793203200,
                            "regularMarketChangePercent": -6.0,
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    return handler


def _telegram_requests(fake_http: _FakeHttp) -> list[httpx.Request]:
    return [r for r in fake_http.requests if r.url.host == "api.telegram.org"]


def test_round_crossing_below_sends_telegram_and_marks_alerted(
    alert_env, monkeypatch, fake_http, capsys
):
    """Preço 9.50 ≤ alvo 10.0: aviso sai no Telegram e o item fica alertado (SC-1)."""
    settings, conn = alert_env
    _seed_petr4(conn, target=10.0)
    fake_http.route(_brapi_price(9.50))
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(conn, settings)

    telegram_requests = _telegram_requests(fake_http)
    assert len(telegram_requests) == 1
    payload = json.loads(telegram_requests[0].content)
    assert payload["chat_id"] == _TELEGRAM_CHAT_ID
    assert "Baixou: PETR4" in payload["text"]  # título estável (D-03)
    assert "R$ 9,50" in payload["text"]  # o preço está no corpo, com moeda PT-BR (D-01/D-02)

    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 1
    assert quote["alerted_at"] == "2026-10-28T16:00:00Z"  # hora UTC do disparo (ALERT-04)
    assert quote["alerted_target"] == 10.0
    assert json.loads(quote["notify_state"]) == {"telegram": "ok"}  # formato final (D-15)

    out = capsys.readouterr().out
    assert "alert_sent" in out
    assert _TELEGRAM_TOKEN not in out  # o token nunca sai no log (K2/T-03-01)


def test_round_repeated_below_target_does_not_resend(alert_env, monkeypatch, fake_http):
    """Duas rodadas com o preço ainda 9.50: exatamente UMA requisição ao Telegram (SC-1)."""
    settings, conn = alert_env
    _seed_petr4(conn, target=10.0)
    fake_http.route(_brapi_price(9.50))
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(conn, settings)
    assert len(_telegram_requests(fake_http)) == 1

    worker_main.run_round(conn, settings)

    assert len(_telegram_requests(fake_http)) == 1  # um aviso por cruzamento
    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 1  # o estado persiste entre as rodadas


def test_round_channel_refusal_keeps_item_waiting(alert_env, monkeypatch, fake_http, capsys):
    """Telegram 500: nenhum canal aceitou — só notify_state é gravado.

    A rodada seguinte tenta de novo (SC-3).
    """
    settings, conn = alert_env
    _seed_petr4(conn, target=10.0)
    telegram_up = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.telegram.org":
            if telegram_up["on"]:
                return httpx.Response(200, json={"ok": True})
            return httpx.Response(500, text="boom")
        return _brapi_price(9.50)(request)

    fake_http.route(handler)
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(conn, settings)  # rodada 1: canal recusado

    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 0  # sem canal aceitando, o item não fica alertado
    assert quote["alerted_at"] is None
    assert json.loads(quote["notify_state"]) == {
        "telegram": "erro unavailable (HTTP 500)"
    }  # a falha por canal fica registrada (SC-3, D-15)
    out = capsys.readouterr().out
    assert "alert_delivery_failed" in out
    assert "500" in out  # o código HTTP é citado
    assert "api.telegram.org" not in out  # a URL nunca entra no log (K2)

    telegram_up["on"] = True
    worker_main.run_round(conn, settings)  # rodada 2: canal aceita

    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 1  # a repetição da entrega é na rodada seguinte (D-16)
    assert json.loads(quote["notify_state"]) == {"telegram": "ok"}


def test_round_two_channels_one_accepts_other_fails_records_both(
    alert_env_two_channels, monkeypatch, fake_http, capsys
):
    """e2e (SC-3): Telegram 401 + ntfy 200 — alertado com resultado por canal em notify_state."""
    settings, conn = alert_env_two_channels
    _seed_petr4(conn, target=10.0)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.telegram.org":
            return httpx.Response(401, json={"ok": False, "error_code": 401})
        if request.url.host == "ntfy.sh":
            return httpx.Response(200, json={"id": "abc"})
        return _brapi_price(9.50)(request)

    fake_http.route(handler)
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(conn, settings)

    assert len(_telegram_requests(fake_http)) == 1
    assert len([r for r in fake_http.requests if r.url.host == "ntfy.sh"]) == 1
    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 1  # um canal aceitou: o item fica alertado
    state = json.loads(quote["notify_state"])
    assert state == {"telegram": "erro auth (HTTP 401)", "ntfy": "ok"}
    assert list(state) == ["telegram", "ntfy"]  # chaves na ordem de CHANNEL_ORDER (D-15)
    out = capsys.readouterr().out
    assert out.count("alert_delivery_failed") == 1
    assert out.count("alert_sent") == 1


def test_round_price_above_target_never_notifies(alert_env, monkeypatch, fake_http):
    """Preço acima do alvo: nenhuma requisição ao Telegram e item segue aguardando."""
    settings, conn = alert_env
    _seed_petr4(conn, target=10.0)
    fake_http.route(_brapi_price(38.25))
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(conn, settings)

    assert _telegram_requests(fake_http) == []  # não cruzou para baixo
    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 0
    assert quote["alerted_at"] is None
    assert quote["notify_state"] is None


def test_round_rearm_full_cycle(alert_env, monkeypatch, fake_http):
    """SC-2 e2e: 9.0 alerta → 15.15 rearma sem enviar → 9.0 dispara NOVO aviso.

    Duas primeiras rodadas cobrem o caso do plano (preços 9.0 → 15.15, rearme
    na margem exata do alvo 15.0 com REARM_PCT 1); a terceira prova o arco
    inteiro do valor central — o aviso repete SOMENTE depois do rearme.
    """
    settings, conn = alert_env
    _seed_petr4(conn, target=15.0)
    fake_http.route(_brapi_price(9.0))
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(conn, settings)  # rodada 1: cruzamento → aviso

    assert len(_telegram_requests(fake_http)) == 1
    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 1
    assert quote["alerted_target"] == 15.0
    assert quote["alerted_at"] == "2026-10-28T16:00:00Z"

    fake_http.route(_brapi_price(15.15))  # margem exata do rearme (D-12)
    worker_main.run_round(conn, settings)  # rodada 2: rearme — mudança de estado, sem envio

    assert len(_telegram_requests(fake_http)) == 1  # rearme não gera requisição a canal
    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 0
    assert quote["alerted_target"] is None
    assert quote["alerted_at"] == "2026-10-28T16:00:00Z"  # histórico sobrevive (ALERT-04)

    fake_http.route(_brapi_price(9.0))
    _freeze_clock(monkeypatch, datetime(2026, 10, 28, 16, 15, 0, tzinfo=UTC))
    worker_main.run_round(conn, settings)  # rodada 3: novo cruzamento → novo aviso

    assert len(_telegram_requests(fake_http)) == 2  # o aviso repete só depois do rearme
    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 1
    assert quote["alerted_at"] == "2026-10-28T16:15:00Z"  # novo disparo atualiza o histórico
    assert quote["alerted_target"] == 15.0
