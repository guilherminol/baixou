"""Configuração por variáveis de ambiente (OPS-02).

Stdlib puro — este módulo é importado pelo worker (D-07): proibido dotenv e
pydantic. Uma variável inválida impede a subida nomeando a variável; o valor
lido nunca aparece na mensagem nem no log (K2). Segredos aceitam o sufixo
``_FILE`` (convenção dos secrets do Docker): ``NOME_FILE`` lê o valor de um
arquivo; a variável ``NOME`` direta tem prioridade sobre o arquivo.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from baixou.core.log import log

_VALID_LOG_LEVELS = ("debug", "info", "warning", "error")
_FILE_SUFFIX = "_FILE"


@dataclass(frozen=True)
class Settings:
    """Configuração tipada da Fase 1."""

    data_dir: Path
    tz_market: str
    poll_minutes: int
    log_level: str
    # Pares lidos via _FILE, separados dos campos comuns (fases 2–3 consomem
    # TELEGRAM_TOKEN etc. por aqui). Valores aqui NUNCA vão para log/erro (K2).
    secrets: dict[str, str] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        """Caminho do banco SQLite único (WAL)."""
        return self.data_dir / "baixou.db"


def _fail(variable: str, hint: str = "") -> NoReturn:
    """Registra config_invalid e encerra nomeando apenas a variável (nunca o valor)."""
    message = f"variável de ambiente {variable} inválida — a subida foi interrompida"
    if hint:
        message = f"{message} ({hint})"
    log("error", "config", "config_invalid", text=message)
    raise SystemExit(message)


def _read_secret_file(environ: Mapping[str, str], name: str) -> str:
    """Lê ``NOME_FILE`` removendo UMA newline final.

    Falha de leitura (arquivo ausente, sem permissão, não-UTF-8) é erro próprio
    nomeando a variável _FILE — o conteúdo do arquivo e o caminho nunca chegam
    ao log nem à exceção (K2; arquitetura linha 192).
    """
    file_variable = name + _FILE_SUFFIX
    try:
        value = Path(environ[file_variable]).read_text(encoding="utf-8")
    except OSError, ValueError:
        _fail(file_variable, "o arquivo do segredo não pôde ser lido")
    return value[:-1] if value.endswith("\n") else value


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    """Lê e valida o ambiente; qualquer falha impede a subida (fail-fast)."""
    secrets: dict[str, str] = {}

    def resolve(name: str) -> str | None:
        """Ordem NOME → NOME_FILE → padrão do chamador (arquitetura linha 190).

        NOME direto vence; via NOME_FILE o valor também vai para ``secrets``.
        """
        if name in environ:
            return environ[name]
        if name + _FILE_SUFFIX in environ:
            value = _read_secret_file(environ, name)
            secrets[name] = value
            return value
        return None

    data_dir = Path(resolve("DATA_DIR") or "/data")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".baixou-write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        _fail("DATA_DIR", "a pasta não pôde ser criada nem gravada")

    tz_market = resolve("TZ_MARKET") or "America/Sao_Paulo"
    try:
        ZoneInfo(tz_market)  # nome de zona IANA, nunca offset (Pitfall 5)
    except ZoneInfoNotFoundError, ValueError, KeyError:
        _fail(
            "TZ_MARKET",
            "use o nome de uma zona IANA (ex.: America/Sao_Paulo), nunca um offset",
        )

    try:
        poll_minutes = int((resolve("POLL_MINUTES") or "15").strip())
    except ValueError:
        _fail("POLL_MINUTES", "use um inteiro maior que zero")
    if poll_minutes <= 0:
        _fail("POLL_MINUTES", "use um inteiro maior que zero")

    log_level = (resolve("LOG_LEVEL") or "info").strip().lower()
    if log_level not in _VALID_LOG_LEVELS:
        _fail("LOG_LEVEL", f"use um de: {', '.join(_VALID_LOG_LEVELS)}")

    # Qualquer NOME_FILE no ambiente é resolvido — mesmo sem campo próprio em
    # Settings (fases futuras adicionam nomes; variáveis desconhecidas são
    # toleradas). NOME presente vence: o arquivo nem é lido.
    for variable in environ:
        if variable.endswith(_FILE_SUFFIX):
            base = variable[: -len(_FILE_SUFFIX)]
            if base and base not in environ and base not in secrets:
                resolve(base)

    return Settings(
        data_dir=data_dir,
        tz_market=tz_market,
        poll_minutes=poll_minutes,
        log_level=log_level,
        secrets=secrets,
    )
