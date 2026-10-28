"""Avisos operacionais de ponta a ponta — episódios de fonte e de cota (ALERT-03, D-05, D-06).

Mesmos padrões de test_alert_round.py (``MockTransport`` roteado por host,
relógio congelado, banco recém-migrado) com a rodada real ``run_round``:
brapi falhando (500) em 3 rodadas seguidas (FAIL_NOTIFY_AFTER) dispara UM
aviso de falha; normalizar dispara UM aviso de volta ao normal; um SEGUNDO
ciclo falha→recuperação NO MESMO banco emite os dois avisos DE NOVO — um
aviso por EPISÓDIO, não por lifetime da fonte (ALERT-03): a transição de
recuperação apaga a chave de falha e substitui a de recovery. Flag de
ENTREGA (D-06): canal recusando reenvia a cada rodada até o primeiro aceite;
entregue, nada se refaz dentro do episódio — inclusive alcançando canais
configurados depois do início. A normalização encerra o aviso de falha
pendente SEM envio (informação falsa após a normalização). Avisos
operacionais NUNCA tocam as colunas de quote do item (D-07).
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

_TELEGRAM_TOKEN = "123456789:bot-tok-secreto-xyz"  # formato <id>:<hash> (D-09)
_TELEGRAM_CHAT_ID = "42"


def _freeze_clock(monkeypatch, now_utc: datetime) -> None:
    """Fixa o ``datetime`` do módulo worker.main num instante arbitrário."""

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
def ops_env(tmp_path, monkeypatch) -> tuple[Settings, sqlite3.Connection]:
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


def _seed_item(conn: sqlite3.Connection, target: float = 5.0) -> None:
    """Semeia PETR4 com alvo FORA do alcance dos preços da brapi (sem aviso de preço).

    Alvo 5.0 contra preço 9.50: o cruzamento para baixo nunca acontece — as
    colunas de quote ficam livres para provar que avisos operacionais não as
    tocam (D-07).
    """
    conn.execute(
        "INSERT INTO watch (symbol, market, target_below) VALUES ('PETR4', 'b3', ?)",
        (target,),
    )


def _brapi_price(price: float) -> _Handler:
    """Handler: brapi responde cotação FRESCA (16:00 UTC) e o Telegram aceita."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "brapi.dev":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "symbol": "PETR4",
                            "regularMarketPrice": price,
                            "regularMarketTime": 1793203200,  # 2026-10-28T16:00:00Z
                            "regularMarketChangePercent": -1.0,
                        }
                    ]
                },
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    return handler


def _brapi_failing(telegram_ok: bool = True) -> _Handler:
    """Handler: brapi 500 (unavailable) e o Telegram conforme ``telegram_ok``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "brapi.dev":
            return httpx.Response(500, text="boom")
        if not telegram_ok:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    return handler


def _telegram_with_text(fake_http: _FakeHttp, needle: str) -> list[httpx.Request]:
    """Requisições ao Telegram cujo corpo cita o fragmento (título do aviso)."""
    found = []
    for request in fake_http.requests:
        if request.url.host != "api.telegram.org":
            continue
        if needle in json.loads(request.content)["text"]:
            found.append(request)
    return found


def _episode(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT delivered FROM notify_episode WHERE key = ?", (key,)).fetchone()


def test_source_failure_recovery_full_two_cycles(ops_env, monkeypatch, fake_http, capsys):
    """3 rodadas de falha → 1 aviso; normaliza → 1 recuperação; SEGUNDO ciclo repete os DOIS.

    ALERT-03/SC-4/D-05a+b: um aviso por EPISÓDIO — a transição de recuperação
    APAGA a chave de falha e substitui a de recovery, então o próximo ciclo
    falha→recuperação da mesma fonte emite os dois avisos DE NOVO.
    """
    settings, conn = ops_env
    _seed_item(conn)
    fake_http.route(_brapi_failing())
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)

    worker_main.run_round(conn, settings)  # rodada 1: consecutive_failures = 1
    worker_main.run_round(conn, settings)  # rodada 2: 2 — abaixo do limiar
    assert _telegram_with_text(fake_http, "com falha") == []

    worker_main.run_round(conn, settings)  # rodada 3: 3 ≥ FAIL_NOTIFY_AFTER → aviso
    assert len(_telegram_with_text(fake_http, "com falha")) == 1
    assert _episode(conn, "source:brapi:failure")["delivered"] == 1  # Telegram aceitou

    worker_main.run_round(conn, settings)  # rodada 4: ainda falhando — NÃO repete
    assert len(_telegram_with_text(fake_http, "com falha")) == 1  # flag delivered (D-06)

    fake_http.route(_brapi_price(9.50))
    worker_main.run_round(conn, settings)  # rodada 5: normaliza → recuperação
    assert len(_telegram_with_text(fake_http, "normalizada")) == 1
    assert _episode(conn, "source:brapi:failure") is None  # chave de falha APAGADA
    assert _episode(conn, "source:brapi:recovery")["delivered"] == 1

    worker_main.run_round(conn, settings)  # rodada 6: ok — episódio fechado, nada se refaz
    assert len(_telegram_with_text(fake_http, "normalizada")) == 1

    # SEGUNDO ciclo no MESMO banco (continuidade de estado): avisa DE NOVO.
    fake_http.route(_brapi_failing())
    worker_main.run_round(conn, settings)  # rodada 7: consecutive_failures = 1
    worker_main.run_round(conn, settings)  # rodada 8: 2
    assert len(_telegram_with_text(fake_http, "com falha")) == 1
    worker_main.run_round(conn, settings)  # rodada 9: 3 → NOVO aviso de falha
    assert len(_telegram_with_text(fake_http, "com falha")) == 2

    fake_http.route(_brapi_price(9.50))
    worker_main.run_round(conn, settings)  # rodada 10: normaliza → NOVA recuperação
    assert len(_telegram_with_text(fake_http, "normalizada")) == 2

    out = capsys.readouterr().out
    assert out.count("source_notify_alert") == 4  # 2 falhas + 2 recuperações
    assert _TELEGRAM_TOKEN not in out  # nunca URL/token no log (K2)

    # D-07: avisos operacionais não tocam NENHUMA coluna de quote do item.
    quote = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    assert quote["alerted"] == 0
    assert quote["alerted_at"] is None
    assert quote["alerted_target"] is None
    assert quote["notify_state"] is None


def test_failure_pending_retried_every_round_then_discarded_by_recovery(
    ops_env, monkeypatch, fake_http
):
    """Canal sempre 500: falha pendente re-tenta TODA rodada; normalização encerra sem envio.

    D-06: pendente repete até o primeiro aceite (requisições crescem). Ao
    normalizar, o aviso de falha pendente é APAGADO SEM envio — reenviar
    "fonte com falha" depois da normalização seria informação falsa — e o
    episódio passa à recuperação.
    """
    settings, conn = ops_env
    _seed_item(conn)
    fake_http.route(_brapi_failing(telegram_ok=False))
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)

    for _ in range(3):  # rodadas 1–3: limiar alcançado, 1ª tentativa (recusada)
        worker_main.run_round(conn, settings)
    assert len(_telegram_with_text(fake_http, "com falha")) == 1
    assert _episode(conn, "source:brapi:failure")["delivered"] == 0

    worker_main.run_round(conn, settings)  # rodada 4: pendente re-tenta
    worker_main.run_round(conn, settings)  # rodada 5: e de novo — requisições crescem
    assert len(_telegram_with_text(fake_http, "com falha")) == 3
    assert _episode(conn, "source:brapi:failure")["delivered"] == 0  # só vira 1 com aceite

    channel_up = {"on": False}

    def recovering(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.telegram.org" and not channel_up["on"]:
            return httpx.Response(500, text="boom")
        return _brapi_price(9.50)(request)

    fake_http.route(recovering)
    worker_main.run_round(conn, settings)  # rodada 6: normaliza — pendente encerrado SEM envio
    assert len(_telegram_with_text(fake_http, "com falha")) == 3  # zero novas tentativas de falha
    assert _episode(conn, "source:brapi:failure") is None
    assert len(_telegram_with_text(fake_http, "normalizada")) == 1  # passou à recuperação
    assert _episode(conn, "source:brapi:recovery")["delivered"] == 0  # canal ainda recusa

    worker_main.run_round(conn, settings)  # rodada 7: recuperação pendente re-tenta
    assert len(_telegram_with_text(fake_http, "normalizada")) == 2
    assert _episode(conn, "source:brapi:recovery")["delivered"] == 0

    channel_up["on"] = True
    worker_main.run_round(conn, settings)  # rodada 8: canal aceita → entregue
    assert len(_telegram_with_text(fake_http, "normalizada")) == 3
    assert _episode(conn, "source:brapi:recovery")["delivered"] == 1  # só com aceite

    worker_main.run_round(conn, settings)  # rodada 9: episódio entregue — nada mais
    assert len(_telegram_with_text(fake_http, "normalizada")) == 3


def test_recovery_pending_repeats_until_first_acceptance(ops_env, monkeypatch, fake_http):
    """Transição com canal recusando: recuperação re-envia a cada rodada ok até o aceite.

    Uma tentativa POR rodada (sem retry em laço — D-16); após a entrega,
    nada se refaz dentro do episódio — nem com o canal de volta.
    """
    settings, conn = ops_env
    _seed_item(conn)
    fake_http.route(_brapi_failing(telegram_ok=True))
    _freeze_clock(monkeypatch, _MARKET_OPEN_NOW)

    for _ in range(3):  # rodadas 1–3: falha alertada com canal no ar
        worker_main.run_round(conn, settings)
    assert len(_telegram_with_text(fake_http, "com falha")) == 1
    assert _episode(conn, "source:brapi:failure")["delivered"] == 1

    telegram_up = {"on": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.telegram.org" and not telegram_up["on"]:
            return httpx.Response(500, text="boom")
        return _brapi_price(9.50)(request)

    fake_http.route(handler)
    worker_main.run_round(conn, settings)  # rodada 4: normaliza com canal FORA
    assert len(_telegram_with_text(fake_http, "normalizada")) == 1
    assert _episode(conn, "source:brapi:recovery")["delivered"] == 0

    worker_main.run_round(conn, settings)  # rodada 5: pendente re-tenta (uma por rodada)
    assert len(_telegram_with_text(fake_http, "normalizada")) == 2

    telegram_up["on"] = True
    worker_main.run_round(conn, settings)  # rodada 6: canal aceita → entregue
    assert len(_telegram_with_text(fake_http, "normalizada")) == 3
    assert _episode(conn, "source:brapi:recovery")["delivered"] == 1

    worker_main.run_round(conn, settings)  # rodada 7: episódio entregue — nada mais
    assert len(_telegram_with_text(fake_http, "normalizada")) == 3
