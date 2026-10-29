"""Agendador alinhado ao relógio civil (Pattern 2) e espera interrompível.

``next_slot`` vive no núcleo comum desde o plano 05-01 (D-04 — molde
``currency_for``): a fonte ÚNICA do cálculo é ``baixou.core.schedule`` e este
módulo só a reexporta para os imports do worker (e do test_worker.py)
seguintem válidos sem edição. A espera acontece em fatias de até 60 s via
``threading.Event``, recalculando o restante a cada fatia — passos de NTP
são absorvidos.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from baixou.core.schedule import next_slot as next_slot  # reexport (D-04)

_MAX_SLICE_S = 60.0


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
