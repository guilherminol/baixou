"""Próximo slot alinhado ao relógio civil — núcleo comum stdlib-puro (D-04).

Movida de ``worker/scheduler.py`` no plano 05-01 (D-04): a próxima rodada
exibida pelo web (GET /api/status) é derivada com a MESMA função do
agendador — fonte ÚNICA de verdade na fronteira entre os dois serviços,
precedente ``currency_for`` (core/currency.py, 04-02). Módulo 100% stdlib,
só ``datetime`` e ``zoneinfo``, sem I/O: o worker (sem FastAPI/pydantic) e o
web podem importá-lo sem puxar um ao outro — o teste de fronteira
(test_import_boundary.py) e o ruff TID251 cobram isso. ``worker/scheduler.py``
mantém o reexport e ``wait_until``; slots perdidos nunca são recuperados
(Anti-Pattern 3) — ``next_slot`` sempre devolve o próximo slot estritamente
futuro. ``is_backup_due`` (Fase 6, 06-04) é consumida apenas pelo worker
(06-06), mas vive no core pelo mesmo motivo de ``next_slot``: relógio civil é
cálculo puro da fronteira, fonte única testável sem rede.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


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


def is_backup_due(now_utc: datetime, hour: int, tz: ZoneInfo) -> bool:
    """Backup noturno já vence nesta virada de dia? (molde ``next_slot`` — pura, sem I/O).

    A conta é feita na HORA CIVIL de ``now_utc`` convertida para o fuso ``tz``
    (não no UTC): a borda é INCLUSIVA — exatamente na hora marcada já vence
    (precedente D-10/D-12). Depois da hora, no mesmo dia, segue vencendo;
    a virada de dia recomeça o ciclo até a hora. ``hour`` fora de 0–23 é
    erro de programação: ValueError no molde de ``next_slot``.
    """
    if not 0 <= hour <= 23:
        raise ValueError("hour deve estar entre 0 e 23")
    return now_utc.astimezone(tz).hour >= hour
