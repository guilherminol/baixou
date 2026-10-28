"""Notificador ntfy com servidor simulado — sem rede (NOTIF-01, D-16, T-03-02).

``httpx.MockTransport`` gravando requisições (padrão do telegram.py e das
fontes): envio ok grava o POST JSON com topic/title/body no host de
NTFY_URL; o token opcional vira "Authorization: Bearer" só quando definido;
o "click" entra só com NOTIFY_CLICK_URL preenchido (D-14); falhas
401/500/429 (e os demais 4xx) DEVOLVEM ``NotificationResult`` sem levantar —
nem a URL nem o tópico aparecem em resultado algum (o tópico é a senha do
canal público — T-03-02); erro de transporte devolve kind "unavailable";
UMA requisição por chamada (sem retry em laço — D-16).
``configured_channels`` costura telegram → ntfy na ordem de CHANNEL_ORDER e
devolve lista vazia quando nenhum canal está configurado (avisos
desligados — NOTIF-01).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from baixou.core.config import Settings
from baixou.worker.notifiers import base, ntfy

_Handler = Callable[[httpx.Request], httpx.Response]

_TOPIC = "topico-secreto-baixou"
_TOKEN = "ntfy-tok-123"
_CLICK_URL = "https://exemplo.test/abrir"
_NTFY_URL = "https://ntfy.local"


def _settings(
    topic: str = _TOPIC,
    token: str = "",
    click_url: str = "",
    ntfy_url: str = _NTFY_URL,
    telegram_token: str = "",
    telegram_chat_id: str = "",
) -> Settings:
    return Settings(
        data_dir=Path("."),  # irrelevante para o notificador
        tz_market="America/Sao_Paulo",
        poll_minutes=15,
        log_level="info",
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
        ntfy_url=ntfy_url,
        ntfy_topic=topic,
        ntfy_token=token,
        notify_click_url=click_url,
    )


def _mock_client(handler: _Handler, requests: list[httpx.Request]) -> httpx.Client:
    """Client com MockTransport que registra cada requisição disparada."""

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    return httpx.Client(transport=httpx.MockTransport(recording))


def test_send_happy_path_posts_json_with_topic_title_and_body():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "abc123"})

    with _mock_client(handler, requests) as client:
        result = ntfy.send(client, _settings(), "Baixou: PETR4", "Preço atual: R$ 9,50")

    assert result.ok is True
    assert result.kind is None
    assert result.http_status == 200
    assert len(requests) == 1  # uma requisição por chamada (sem retry — D-16)
    request = requests[0]
    assert request.url.host == "ntfy.local"  # o host vem de settings.ntfy_url
    assert request.url.path == "/"
    payload = json.loads(request.content)
    assert payload == {
        "topic": _TOPIC,
        "title": "Baixou: PETR4",  # título obrigatório — nunca o tópico (T-03-02)
        "body": "Preço atual: R$ 9,50",
    }
    assert "authorization" not in {key.lower() for key in request.headers}


def test_send_with_token_sends_bearer_authorization_header():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "abc123"})

    with _mock_client(handler, requests) as client:
        result = ntfy.send(client, _settings(token=_TOKEN), "Baixou: PETR4", "corpo")

    assert result.ok is True
    assert requests[0].headers["Authorization"] == f"Bearer {_TOKEN}"  # D-14


def test_click_enters_payload_only_with_notify_click_url():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "abc123"})

    with _mock_client(handler, requests) as client:
        result = ntfy.send(client, _settings(click_url=_CLICK_URL), "Baixou: PETR4", "corpo")

    assert result.ok is True
    payload = json.loads(requests[0].content)
    assert payload["click"] == _CLICK_URL  # D-14: vazio omite o botão de abrir


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "auth"), (429, "rate_limit"), (500, "unavailable"), (400, "unexpected")],
)
def test_send_failure_status_returns_result_without_raising_or_leaking(status, kind):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"code": status})

    with _mock_client(handler, requests) as client:
        result = ntfy.send(client, _settings(), "Baixou: PETR4", "corpo")

    assert result.ok is False  # falha devolve resultado — nenhum pytest.raises (D-16)
    assert result.kind == kind
    assert result.http_status == status
    assert len(requests) == 1  # sem retry em laço: a rodada seguinte decide (D-16)
    rendered = repr(result) + str(result)
    assert "ntfy.local" not in rendered  # a URL nunca entra no resultado (T-03-02)
    assert _TOPIC not in rendered  # o tópico é a senha do canal público (T-03-02)


def test_send_transport_error_returns_unavailable_without_url_or_topic():
    """Erro de transporte → unavailable; nem a URL nem o tópico vazam (T-03-02)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with _mock_client(handler, requests) as client:
        result = ntfy.send(client, _settings(token=_TOKEN), "Baixou: PETR4", "corpo")

    assert result.ok is False
    assert result.kind == "unavailable"
    assert result.http_status is None
    rendered = repr(result) + str(result)
    assert "ntfy.local" not in rendered  # a URL nunca aparece (K2)
    assert _TOPIC not in rendered and _TOKEN not in rendered  # tópico/token nunca (T-03-02)


@pytest.mark.parametrize(("topic", "expected"), [(_TOPIC, True), ("", False)])
def test_is_configured_requires_topic(topic, expected):
    assert ntfy.is_configured(_settings(topic=topic)) is expected  # D-08: tópico basta


def test_configured_channels_stitches_telegram_then_ntfy():
    settings = _settings(telegram_token="123456789:tok", telegram_chat_id="42")
    channels = base.configured_channels(settings)
    assert [name for name, _send in channels] == ["telegram", "ntfy"]  # ordem CHANNEL_ORDER (D-16)


def test_configured_channels_excludes_ntfy_without_topic():
    settings = _settings(topic="", telegram_token="123456789:tok", telegram_chat_id="42")
    channels = base.configured_channels(settings)
    assert [name for name, _send in channels] == ["telegram"]


def test_no_channel_configured_returns_empty_list_and_sends_nothing():
    """Caso "nenhum canal configurado": lista vazia, nenhum envio tentado, sem erro (NOTIF-01)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)  # qualquer requisição aqui é um bug
        return httpx.Response(200, json={})

    settings = _settings(topic="", telegram_token="", telegram_chat_id="")
    with _mock_client(handler, requests) as client:
        channels = base.configured_channels(settings)
        for _name, send in channels:
            send(client, settings, "Baixou: PETR4", "corpo")

    assert channels == []  # avisos desligados (NOTIF-01, truth 6)
    assert requests == []  # nenhuma requisição sai
