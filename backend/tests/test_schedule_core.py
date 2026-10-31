"""``next_slot`` no núcleo comum (plano 05-01, D-04) — fronteira e comportamento.

Dois testes:

- **Fronteira** via subprocesso (nunca import in-process: o sys.modules do
  pytest já vem poluído — molde test_import_boundary.py): importar
  ``baixou.core.schedule`` num processo limpo só sai 0 se fastapi e pydantic
  NÃO estiverem em sys.modules — o módulo é 100% stdlib (datetime +
  zoneinfo) para worker e web o consumirem como fonte ÚNICA do cálculo de
  slot sem puxar um ao outro.
- **Comportamento** (molde test_worker.py): um agora EXATO num slot devolve
  o PRÓXIMO slot, nunca o corrente; minutes <= 0 segue recusado com
  ValueError. Os demais casos de borda (virada de dia, slot perdido, DST)
  permanecem cobertos por test_worker.py SEM edição — o reexport em
  worker/scheduler.py é transparente.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from baixou.core.schedule import is_backup_due, next_slot

_TIMEOUT_S = 120

_PROBE = """\
import sys

import baixou.core.schedule

prohibited = [name for name in ("fastapi", "pydantic") if name in sys.modules]
if prohibited:
    sys.exit(f"fronteira violada: {prohibited} em sys.modules do core.schedule")
print("boundary-ok")
"""


def _run_probe(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
    )


def test_core_schedule_import_leaves_no_web_framework_in_sys_modules():
    """Core/schedule é stdlib-puro: fastapi/pydantic ausentes do sys.modules (D-04)."""
    result = _run_probe(_PROBE)
    assert result.returncode == 0, result.stderr


def test_next_slot_at_exact_slot_returns_next_never_current():
    """Molde test_worker.py: agora exato no slot → o PRÓXIMO, nunca o corrente."""
    tz = ZoneInfo("UTC")
    now = datetime(2026, 10, 29, 15, 0, 0, tzinfo=UTC)
    assert next_slot(now, 15, tz) == datetime(2026, 10, 29, 15, 15, tzinfo=UTC)


def test_next_slot_recusa_minutes_nao_positivo():
    """Contrato preservado na mudança: minutes <= 0 é ValueError (docstring)."""
    tz = ZoneInfo("UTC")
    now = datetime(2026, 10, 29, 15, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        next_slot(now, 0, tz)


# --- Fase 6 (plano 06-04, Task 2): is_backup_due — relógio puro do backup ---


def test_is_backup_due_before_local_hour_is_false():
    """Agora antes da hora local de hoje → ainda não vence (03:00 locais < 4h)."""
    tz = ZoneInfo("America/Sao_Paulo")  # UTC-3 o ano todo (sem DST desde 2019)
    now = datetime(2026, 10, 29, 6, 0, 0, tzinfo=UTC)  # 03:00 locais
    assert is_backup_due(now, 4, tz) is False


def test_is_backup_due_exactly_at_local_hour_is_true():
    """Borda inclusiva (D-10/D-12): exatamente na hora local → True."""
    tz = ZoneInfo("America/Sao_Paulo")
    now = datetime(2026, 10, 29, 7, 0, 0, tzinfo=UTC)  # 04:00 locais exatos
    assert is_backup_due(now, 4, tz) is True


def test_is_backup_due_after_local_hour_same_day_is_true():
    """Depois da hora no mesmo dia → True (a janela do dia já venceu)."""
    tz = ZoneInfo("America/Sao_Paulo")
    now = datetime(2026, 10, 29, 12, 30, 0, tzinfo=UTC)  # 09:30 locais
    assert is_backup_due(now, 4, tz) is True


def test_is_backup_due_new_day_resets_until_hour():
    """Virada de dia recomeça: 00:30 local → False; 04:00 local do novo dia → True."""
    tz = ZoneInfo("America/Sao_Paulo")
    before = datetime(2026, 10, 30, 3, 30, 0, tzinfo=UTC)  # 00:30 locais do dia 30
    assert is_backup_due(before, 4, tz) is False
    due = datetime(2026, 10, 30, 7, 0, 0, tzinfo=UTC)  # 04:00 locais do dia 30
    assert is_backup_due(due, 4, tz) is True


def test_is_backup_due_recusa_hour_fora_da_faixa():
    """Molde next_slot: hour fora de 0–23 é ValueError (contrato do docstring)."""
    tz = ZoneInfo("UTC")
    now = datetime(2026, 10, 29, 15, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        is_backup_due(now, 24, tz)
    with pytest.raises(ValueError):
        is_backup_due(now, -1, tz)
