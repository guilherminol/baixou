"""Log de uma linha por evento (D-01/D-02).

Formato exato::

    <ts UTC ISO-8601 com Z> <LEVEL> <componente> <evento> [chave=valor ...] — <texto PT-BR opcional>

Nome do evento é estável em inglês; o texto humano é PT-BR. Saída só no stdout
(o Docker cuida da rotação). Durações sempre medidas com ``time.monotonic()``
pelos chamadores. Nunca registra tokens, senhas ou valores de segredo.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_min_level = _LEVELS["info"]


def configure(level: str) -> None:
    """Define o nível mínimo do log e garante stdout em UTF-8 (Windows)."""
    global _min_level
    normalized = level.strip().lower()
    if normalized not in _LEVELS:
        normalized = "info"
    _min_level = _LEVELS[normalized]
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError, ValueError):
        pass


def _format_value(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def log(
    level: str,
    component: str,
    event: str,
    text: str | None = None,
    **fields: object,
) -> None:
    """Escreve um evento de uma linha no stdout."""
    numeric = _LEVELS.get(level.strip().lower())
    if numeric is None or numeric < _min_level:
        return
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [timestamp, level.strip().upper(), component, event]
    parts.extend(f"{key}={_format_value(value)}" for key, value in fields.items())
    line = " ".join(parts)
    if text:
        line = f"{line} — {text}"
    print(line, flush=True)
