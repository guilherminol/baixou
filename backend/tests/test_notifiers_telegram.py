"""Notificador Telegram com servidor simulado — sem rede (D-16, T-03-01).

``httpx.MockTransport`` gravando requisições (padrão das fontes): envio ok
grava o POST no host api.telegram.org com título e corpo no payload; falhas
401/500/429 (e os demais 4xx) DEVOLVEM ``NotificationResult`` sem levantar
exceção; erro de transporte devolve kind "unavailable" e nem a URL nem o
token aparecem em resultado ou traceback; UMA requisição por chamada (sem
retry em laço — D-16).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import FrozenInstanceError
from pathlib import Path

import httpx
import pytest
from baixou.core.config import Settings
from baixou.worker.notifiers import telegram
from baixou.worker.notifiers.base import NotificationResult

_Handler = Callable[[httpx.Request], httpx.Response]

_TOKEN = "bot-tok-123"
_CHAT_ID = "42"


def _settings(token: str = _TOKEN, chat_id: str = _CHAT_ID) -> Settings:
    return Settings(
        data_dir=Path("."),  # irrelevante para o notificador
        tz_market="America/Sao_Paulo",
        poll_minutes=15,
        log_level="info",
        telegram_token=token,
        telegram_chat_id=chat_id,
    )


def _mock_client(handler: _Handler, requests: list[httpx.Request]) -> httpx.Client:
    """Client com MockTransport que registra cada requisição disparada."""

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(recording))


def test_send_happy_path_posts_title_and_body():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    with _mock_client(handler, requests) as client:
        result = telegram.send(client, _settings(), "Baixou: PETR4", "Preço atual: 9.50")

    assert result.ok is True
    assert result.kind is None
    assert result.http_status == 200
    assert len(requests) == 1  # uma requisição por chamada (sem retry — D-16)
    request = requests[0]
    assert request.url.host == "api.telegram.org"
    assert request.url.path == f"/bot{_TOKEN}/sendMessage"
    payload = json.loads(request.content)
    assert payload["chat_id"] == _CHAT_ID
    assert payload["text"] == "Baixou: PETR4\n\nPreço atual: 9.50"
    assert "parse_mode" not in payload  # texto puro


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "unavailable"), (400, "unexpected")],
)
def test_send_failure_status_returns_result_without_raising(status, kind):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"description": "boom"})

    with _mock_client(handler, requests) as client:
        result = telegram.send(client, _settings(), "Baixou: PETR4", "corpo")

    assert result.ok is False
    assert result.kind == kind
    assert result.http_status == status
    assert len(requests) == 1  # sem retry em laço: a rodada seguinte decide (D-16)


def test_send_transport_error_returns_unavailable_without_url_or_token():
    """Erro de transporte → unavailable; nem a URL nem o token vazam (T-03-01)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _mock_client(handler, requests) as client:
        result = telegram.send(client, _settings(), "Baixou: PETR4", "corpo")

    assert result.ok is False
    assert result.kind == "unavailable"
    assert result.http_status is None
    rendered = repr(result) + str(result)  # nenhuma representação carrega segredo
    assert "api.telegram.org" not in rendered  # a URL com o token no path (K2)
    assert _TOKEN not in rendered and _CHAT_ID not in rendered


@pytest.mark.parametrize(
    ("token", "chat_id", "expected"),
    [
        (_TOKEN, _CHAT_ID, True),
        ("", _CHAT_ID, False),  # sem token: canal pela metade (D-08)
        (_TOKEN, "", False),  # sem chat_id: canal pela metade (D-08)
        ("", "", False),  # sem canal nenhum: avisos desligados
    ],
)
def test_is_configured_requires_token_and_chat_id(token, chat_id, expected):
    assert telegram.is_configured(_settings(token=token, chat_id=chat_id)) is expected


def test_notification_result_is_frozen():
    result = NotificationResult(ok=True, kind=None, http_status=200)
    with pytest.raises(FrozenInstanceError):
        result.ok = False  # type: ignore[misc]
