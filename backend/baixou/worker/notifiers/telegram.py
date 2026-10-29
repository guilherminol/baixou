"""Notificador Telegram: POST JSON no sendMessage, resultado sem exceção (D-16).

O token viaja no PATH da URL (``https://api.telegram.org/bot{token}/sendMessage``),
o que torna a não-propagação de exceções ainda mais crítica que nas fontes
(T-03-01): a exceção httpx carrega a requisição completa — então NENHUM
caminho de falha levanta — o resultado tipado substitui a exceção e as
mensagens de falha citam apenas o kind e o código HTTP, nunca a URL, o token
ou o chat_id (K2/T-02-04/T-03-01). Texto puro, sem parse_mode; sem retry
dentro da rodada (D-16) — a repetição é na rodada seguinte.
"""

from __future__ import annotations

import httpx

from baixou.core.config import Settings
from baixou.worker.notifiers.base import NotificationResult

TELEGRAM_URL = "https://api.telegram.org/bot{token}/sendMessage"

_AUTH_STATUS = frozenset({401, 403})
_RATE_LIMIT_STATUS = frozenset({429})


def is_configured(settings: Settings) -> bool:
    """Canal Telegram configurado: token E chat_id não vazios (D-08/D-14)."""
    return bool(settings.telegram_token) and bool(settings.telegram_chat_id)


def send(client: httpx.Client, settings: Settings, title: str, body: str) -> NotificationResult:
    """Envia título+corpo por ``sendMessage`` e devolve o resultado tipado (D-16).

    2xx → ok; 401/403 → auth; 429 → rate_limit; ≥500 → unavailable; outros
    4xx → unexpected; erro de transporte → unavailable sem código HTTP. A
    exceção httpx NUNCA é re-levantada nem encadeada (o papel do ``from None``
    das fontes): ela carrega a URL com o token no path e não pode escapar nem
    por traceback (T-03-01).
    """
    url = TELEGRAM_URL.format(token=settings.telegram_token)
    payload = {"chat_id": settings.telegram_chat_id, "text": f"{title}\n\n{body}"}
    try:
        response = client.post(url, json=payload)
    except httpx.TransportError:
        # A exceção carrega a URL com o token no path — não é re-levantada nem
        # encadeada: o resultado tipado a substitui (D-16, T-03-01).
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
