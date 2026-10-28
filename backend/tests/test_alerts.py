"""Máquina de estados do alerta SEM rede — tabela completa da arquitetura.

Mesmos padrões dos demais testes do worker (banco recém-migrado, seed por SQL
direto em autocommit, relógio congelado, ``MockTransport`` no ``httpx.Client``
passado ao ``process_alerts``): prova a regra "≥1 canal aceitou" com resultado
por canal gravado (SC-3, D-15/D-16), o rearme pela margem REARM_PCT e a troca
de alvo com ``alerted_at`` histórico (SC-2, D-12/D-13, ALERT-04) e o gate de
frescor por idade ok/stale/closed/none (D-11, SC-5, ALERT-02) — cada task
deste plano acrescenta suas linhas da tabela.
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
from baixou.worker import alerts

_Handler = Callable[[httpx.Request], httpx.Response]

# Quarta-feira 16:00 UTC = 13:00 em Brasília: dentro do pregão da B3 (SC-1).
_MARKET_OPEN_NOW = datetime(2026, 10, 28, 16, 0, 0, tzinfo=UTC)

_TELEGRAM_TOKEN = "123456789:bot-tok-secreto-xyz"  # formato <id>:<hash> exigido na subida (D-09)
_TELEGRAM_CHAT_ID = "42"
_NTFY_TOPIC = "baixou-testes"

# price_at das cotações semeadas: 10 minutos antes do relógio congelado —
# fresca por construção nos limites da B3 (75 min) e da cripto (30 min).
_FRESH_PRICE_AT = "2026-10-28T15:50:00Z"


def _configure_env(
    monkeypatch, tmp_path, *, telegram: bool = True, ntfy: bool = True
) -> Settings:
    """Envs de teste com os canais pedidos; devolve Settings carregadas."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ_MARKET", "America/Sao_Paulo")
    monkeypatch.setenv("POLL_MINUTES", "15")
    monkeypatch.setenv("LOG_LEVEL", "info")
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    if telegram:
        monkeypatch.setenv("TELEGRAM_TOKEN", _TELEGRAM_TOKEN)
        monkeypatch.setenv("TELEGRAM_CHAT_ID", _TELEGRAM_CHAT_ID)
    if ntfy:
        monkeypatch.setenv("NTFY_TOPIC", _NTFY_TOPIC)
    return load_settings()


@pytest.fixture
def both_channels(tmp_path, monkeypatch) -> tuple[Settings, sqlite3.Connection]:
    """Settings com Telegram E ntfy configurados + banco recém-migrado."""
    settings = _configure_env(monkeypatch, tmp_path, telegram=True, ntfy=True)
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    yield settings, conn
    conn.close()


def _seed_item(
    conn: sqlite3.Connection,
    *,
    symbol: str = "PETR4",
    market: str = "b3",
    target: float = 10.0,
    price: float | None = 9.50,
    price_at: str | None = _FRESH_PRICE_AT,
    change_pct: float | None = -6.0,
    alerted: int = 0,
    alerted_target: float | None = None,
    last_fetch_error: str | None = None,
) -> None:
    """Semeia watch + quote por SQL direto (isolation_level=None: autocommit)."""
    conn.execute(
        "INSERT INTO watch (symbol, market, target_below) VALUES (?, ?, ?)",
        (symbol, market, target),
    )
    conn.execute(
        """
        INSERT INTO quote (symbol, market, price, price_at, change_pct,
            alerted, alerted_target, last_fetch_error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (symbol, market, price, price_at, change_pct, alerted, alerted_target, last_fetch_error),
    )


def _quote_row(conn: sqlite3.Connection, symbol: str = "PETR4", market: str = "b3") -> sqlite3.Row:
    return conn.execute(
        "SELECT * FROM quote WHERE symbol = ? AND market = ?", (symbol, market)
    ).fetchone()


def _client(handler: _Handler) -> httpx.Client:
    """Cliente httpx real com o transporte falso roteado pelo handler do teste."""
    return httpx.Client(transport=httpx.MockTransport(handler))


class _Recorder:
    """Transporte falso com recorder de requisições e rota definida no teste."""

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


def _recorder_client(recorder: _Recorder) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(recorder.dispatch))


# --- Task 1 (SC-3, D-15/D-16): regra "≥1 canal aceitou" com resultado por canal ---


def test_one_channel_accepts_other_fails_alerts_and_records_both(both_channels, capsys):
    """Telegram 401 + ntfy 200: alertado, notify_state com os DOIS canais (SC-3)."""
    settings, conn = both_channels
    _seed_item(conn)  # PETR4 a 9.50 ≤ alvo 10.0

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.telegram.org":
            return httpx.Response(401, json={"ok": False, "error_code": 401})
        return httpx.Response(200, json={"id": "abc"})  # ntfy aceita

    with _client(handler) as client:
        alerts.process_alerts(conn, settings, client, _MARKET_OPEN_NOW)

    quote = _quote_row(conn)
    assert quote["alerted"] == 1  # um canal aceitou: o item conta como alertado
    assert quote["alerted_at"] == "2026-10-28T16:00:00Z"
    assert quote["alerted_target"] == 10.0
    state = json.loads(quote["notify_state"])
    assert state == {"telegram": "erro auth (HTTP 401)", "ntfy": "ok"}
    assert list(state) == ["telegram", "ntfy"]  # chaves na ordem de CHANNEL_ORDER (D-15)
    out = capsys.readouterr().out
    assert out.count("alert_delivery_failed") == 1  # um evento por canal recusado
    assert out.count("alert_sent") == 1
    assert _TELEGRAM_TOKEN not in out  # nunca URL/token no log (K2/T-03-01)


def test_all_channels_refuse_keeps_waiting_and_retries_next_round(both_channels, capsys):
    """Todos recusam: alerted=0 com notify_state gravado; a rodada seguinte reenvia (SC-3)."""
    settings, conn = both_channels
    _seed_item(conn)
    recorder = _Recorder()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")  # os dois recusam

    recorder.route(handler)

    with _recorder_client(recorder) as client:
        alerts.process_alerts(conn, settings, client, _MARKET_OPEN_NOW)

    quote = _quote_row(conn)
    assert quote["alerted"] == 0  # sem NENHUM aceite, o item NÃO fica alertado
    assert quote["alerted_at"] is None
    assert json.loads(quote["notify_state"]) == {
        "telegram": "erro unavailable (HTTP 500)",
        "ntfy": "erro unavailable (HTTP 500)",
    }
    out = capsys.readouterr().out
    assert out.count("alert_delivery_failed") == 2
    assert "alert_sent" not in out

    with _recorder_client(recorder) as client:
        alerts.process_alerts(conn, settings, client, _MARKET_OPEN_NOW)

    quote = _quote_row(conn)
    assert quote["alerted"] == 0  # segue aguardando: retry na rodada seguinte
    assert len(recorder.requests) == 4  # os DOIS canais reenviam (sem reenvio seletivo)
    assert json.loads(quote["notify_state"]) == {
        "telegram": "erro unavailable (HTTP 500)",
        "ntfy": "erro unavailable (HTTP 500)",
    }


def test_empty_channels_list_sends_and_writes_nothing(tmp_path, monkeypatch):
    """Nenhum canal configurado (lista vazia): nenhum envio e nenhuma escrita (D-08)."""
    settings = _configure_env(monkeypatch, tmp_path, telegram=False, ntfy=False)
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        _seed_item(conn)

        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("nenhuma requisição deve sair sem canal configurado")

        alerts.process_alerts(conn, settings, _client(handler), _MARKET_OPEN_NOW)

        quote = _quote_row(conn)
        assert quote["alerted"] == 0
        assert quote["notify_state"] is None  # nenhuma escrita no item
    finally:
        conn.close()
