"""Configuração por variáveis de ambiente (OPS-02, base).

Stdlib puro — este módulo é importado pelo worker (D-07): proibido dotenv e
pydantic. Uma variável inválida impede a subida nomeando a variável; o valor
lido nunca aparece na mensagem nem no log (K2).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from baixou.core.log import log

_VALID_LOG_LEVELS = ("debug", "info", "warning", "error")


@dataclass(frozen=True)
class Settings:
    """Configuração tipada da Fase 1."""

    data_dir: Path
    tz_market: str
    poll_minutes: int
    log_level: str

    @property
    def db_path(self) -> Path:
        """Caminho do banco SQLite único (WAL)."""
        return self.data_dir / "baixou.db"


def _fail(variable: str) -> None:
    """Registra config_invalid e encerra nomeando apenas a variável (nunca o valor)."""
    message = f"variável de ambiente {variable} inválida — a subida foi interrompida"
    log("error", "config", "config_invalid", text=message)
    raise SystemExit(message)


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    """Lê e valida o ambiente; qualquer falha impede a subida (fail-fast)."""
    data_dir = Path(environ.get("DATA_DIR") or "/data")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".baixou-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        _fail("DATA_DIR")

    tz_market = environ.get("TZ_MARKET") or "America/Sao_Paulo"
    try:
        ZoneInfo(tz_market)  # nome de zona IANA, nunca offset (Pitfall 5)
    except ZoneInfoNotFoundError, ValueError, KeyError:
        _fail("TZ_MARKET")

    try:
        poll_minutes = int((environ.get("POLL_MINUTES") or "15").strip())
    except ValueError:
        _fail("POLL_MINUTES")
    if poll_minutes <= 0:
        _fail("POLL_MINUTES")

    log_level = (environ.get("LOG_LEVEL") or "info").strip().lower()
    if log_level not in _VALID_LOG_LEVELS:
        _fail("LOG_LEVEL")

    return Settings(
        data_dir=data_dir,
        tz_market=tz_market,
        poll_minutes=poll_minutes,
        log_level=log_level,
    )
