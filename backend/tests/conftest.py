"""Fixtures compartilhadas dos testes do backend (sem rede)."""

from __future__ import annotations

import sqlite3

import pytest
from baixou.core.config import Settings, load_settings
from baixou.core.db import WORKER_TIMEOUT_S, connect
from baixou.core.migrations import apply_migrations
from baixou.worker.sources import binance as binance_source


@pytest.fixture(autouse=True)
def _reset_mirror_state():
    """Estado do espelho da Binance (D-04, memória de módulo) zerado entre testes.

    Autouse em toda a suíte: qualquer teste observa o worker tentando o
    endereço principal primeiro, independentemente da ordem de execução.
    """
    state = getattr(binance_source, "_mirror_state", None)
    if state is not None:  # tolerante antes do estado existir (RED da Task 3)
        state.using_mirror = False
        state.last_primary_probe_at = None
    yield
    if state is not None:
        state.using_mirror = False
        state.last_primary_probe_at = None


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    """Settings de teste: DATA_DIR isolado em tmp_path e padrões determinísticos."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ_MARKET", "America/Sao_Paulo")
    monkeypatch.setenv("POLL_MINUTES", "15")
    monkeypatch.setenv("LOG_LEVEL", "info")
    return load_settings()


@pytest.fixture
def db(settings) -> sqlite3.Connection:
    """Banco recém-migrado; fecha a conexão ao terminar."""
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    yield conn
    conn.close()
