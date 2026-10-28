"""Contrato comum dos notificadores: resultado tipado e canais configurados (D-16).

O notificador INVERTE a direção das fontes (arquitetura §Interfaces de fontes
e notificadores): em vez de levantar exceção tipada, devolve um
``NotificationResult`` congelado (ok/kind/http_status) — quem decide a regra
"≥1 canal aceitou" é o worker (worker.alerts), nunca o canal. ``kind`` segue o
vocabulário estável em EN das fontes (auth, rate_limit, unavailable,
unexpected). ``CHANNEL_ORDER`` fixa a ordem de envio entre canais (D-16) e
``configured_channels`` devolve somente os canais com configuração completa,
nessa ordem — nenhum canal configurado devolve lista vazia (avisos
desligados, D-08).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from baixou.core.config import Settings

# Ordem fixa de envio entre canais (D-16). O ntfy entra aqui na expansão do
# plano 03-02 — a costura já é esta lista; nada além dela muda.
CHANNEL_ORDER: tuple[str, ...] = ("telegram", "ntfy")


@dataclass(frozen=True)
class NotificationResult:
    """Resultado de um envio por canal — o canal nunca levanta exceção (D-16)."""

    ok: bool
    kind: str | None  # kind estável em EN quando falha; None no sucesso
    http_status: int | None  # código HTTP da recusa; None em erro de transporte


def configured_channels(settings: Settings) -> list[tuple[str, Callable]]:
    """Canais configurados, na ordem de ``CHANNEL_ORDER`` (D-16).

    "Configurado" é decidido por cada módulo de canal via ``is_configured``.
    O import dos módulos é feito aqui dentro para não fechar ciclo com
    ``base`` (os canais importam ``NotificationResult`` deste módulo).
    """
    from baixou.worker.notifiers import telegram

    channels: list[tuple[str, Callable]] = []
    for channel in CHANNEL_ORDER:
        if channel == "telegram" and telegram.is_configured(settings):
            channels.append(("telegram", telegram.send))
    return channels
