"""Agendador alinhado ao relógio civil (Pattern 2) e espera interrompível.

Slots perdidos nunca são recuperados (Anti-Pattern 3): ``next_slot`` sempre
devolve o próximo slot estritamente futuro. A espera acontece em fatias de até
60 s via ``threading.Event``, recalculando o restante a cada fatia — passos de
NTP são absorvidos.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

_MAX_SLICE_S = 60.0


def next_slot(now_utc: datetime, minutes: int, tz: ZoneInfo) -> datetime:
    """Próximo slot alinhado ao relógio civil no fuso ``tz`` (:00/:15/:30/:45 para 15).

    Devolve o horário em UTC, estritamente futuro em relação a ``now_utc``.
    """
    if minutes <= 0:
        raise ValueError("minutes deve ser positivo")
    step = timedelta(minutes=minutes)
    local = now_utc.astimezone(tz).replace(second=0, microsecond=0)
    midnight = local.replace(hour=0, minute=0)
    elapsed_s = (local - midnight).total_seconds()
    k = int(elapsed_s // (minutes * 60)) + 1
    candidate_local = midnight + step * k
    candidate = candidate_local.astimezone(UTC)
    while candidate <= now_utc:  # segurança para transições de fuso (DST)
        candidate_local = candidate_local + step
        candidate = candidate_local.astimezone(UTC)
    return candidate


def wait_until(target: datetime, stop: threading.Event) -> bool:
    """Dorme até ``target`` em fatias de até 60 s, interrompível por ``stop``.

    Devolve True quando o alvo chega; False se ``stop`` foi acionado antes.
    """
    while not stop.is_set():
        remaining_s = (target - datetime.now(UTC)).total_seconds()
        if remaining_s <= 0:
            return True
        stop.wait(min(remaining_s, _MAX_SLICE_S))
    return False
