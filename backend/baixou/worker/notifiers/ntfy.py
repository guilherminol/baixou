"""Notificador ntfy: POST JSON no publish, resultado sem exceção (D-16, NOTIF-01).

Publica no MESMO contrato do Telegram (D-16): título obrigatório — o tópico
funciona como senha do canal público e NUNCA vira título, corpo, log ou erro
(T-03-02) — e o resultado tipado substitui a exceção. O POST vai para
``{NTFY_URL}/`` com o payload JSON {"topic", "title", "body"}; o endereço de
abrir ("click") entra só com NOTIFY_CLICK_URL definido (D-14: vazio omite) e
o token opcional vira o cabeçalho "Authorization: Bearer" (D-14). Prioridade
e tags ficam de fora (discrição registrada no CONTEXT da fase). Mapa de
status idêntico ao do Telegram: 2xx ok; 401/403 → auth; 429 → rate_limit;
≥500 → unavailable; outros 4xx → unexpected; erro de transporte →
unavailable sem código HTTP. A exceção httpx NUNCA é re-levantada nem
encadeada; as mensagens de falha citam apenas o kind e o código HTTP —
nunca a URL nem o tópico (K2/T-03-02). Sem retry dentro da rodada (D-16) —
a repetição é na rodada seguinte.
"""

from __future__ import annotations

import httpx

from baixou.core.config import Settings
from baixou.worker.notifiers.base import NotificationResult

_AUTH_STATUS = frozenset({401, 403})
_RATE_LIMIT_STATUS = frozenset({429})


def is_configured(settings: Settings) -> bool:
    """Canal ntfy configurado: tópico não vazio (D-08) — URL tem padrão, token é opcional."""
    return bool(settings.ntfy_topic)


def send(client: httpx.Client, settings: Settings, title: str, body: str) -> NotificationResult:
    """Publica título+corpo em JSON no tópico e devolve o resultado tipado (D-16).

    A exceção httpx NUNCA é re-levantada nem encadeada — ela carrega a
    requisição com a URL e o tópico, que não podem escapar nem por traceback
    (T-03-02, como no token do Telegram — T-03-01).
    """
    url = f"{settings.ntfy_url.rstrip('/')}/"
    payload: dict[str, str] = {"topic": settings.ntfy_topic, "title": title, "body": body}
    if settings.notify_click_url:
        payload["click"] = settings.notify_click_url  # vazio omite o botão de abrir (D-14)
    headers = {"Authorization": f"Bearer {settings.ntfy_token}"} if settings.ntfy_token else {}
    try:
        response = client.post(url, json=payload, headers=headers)
    except httpx.TransportError:
        return NotificationResult(ok=False, kind="unavailable", http_status=None)
    status = response.status_code
    if 200 <= status < 300:
        return NotificationResult(ok=True, kind=None, http_status=status)
    if status in _AUTH_STATUS:
        kind = "auth"
    elif status in _RATE_LIMIT_STATUS:
        kind = "rate_limit"
    elif status >= 500:
        kind = "unavailable"
    else:
        kind = "unexpected"
    return NotificationResult(ok=False, kind=kind, http_status=status)
