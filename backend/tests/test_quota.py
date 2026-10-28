"""Cota mensal da brapi: contagem persistida, virada UTC e transições (POLL-03, D-05, D-06).

Unidades da porta e do registro sobre o banco do fixture ``db`` — tudo sem
rede. O mês UTC visto pelo módulo ``quota`` é congelado no atributo
``datetime`` dele (mesmo mecanismo do ``worker.main`` nos testes de rodada):
a virada D-06 compara o mês UTC corrente com a linha gravada, e o teste da
virada usa um instante em que o mês UTC já trocou e o de Brasília ainda não.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

from baixou.core.db import WORKER_TIMEOUT_S, connect, write_tx
from baixou.worker import quota


def _freeze_month(monkeypatch, now_utc: datetime) -> None:
    """Fixa o ``datetime`` do módulo quota num instante arbitrário (determinismo)."""

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return now_utc.astimezone(tz)
            return now_utc.replace(tzinfo=None)

    monkeypatch.setattr(quota, "datetime", _Frozen)


def _quota_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM quota WHERE source = 'brapi'").fetchone()


def test_quota_row_created_on_first_brapi_calls(db, settings, monkeypatch):
    """Banco recém-migrado sem linha: a primeira rodada com chamadas cria a linha."""
    _freeze_month(monkeypatch, datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    assert _quota_row(db) is None

    with write_tx(db):
        quota.record_round_calls(db, settings, 2)

    row = _quota_row(db)
    assert row is not None
    assert row["month"] == "2026-10"  # "YYYY-MM" UTC (D-06)
    assert row["calls"] == 2  # cada fetch_one executado soma 1
    assert row["warned_80"] == 0


def test_quota_calls_accumulate_across_rounds(db, settings, monkeypatch):
    """Rodadas seguintes somam sobre o gravado (2 tickers × 2 rodadas → calls=4)."""
    _freeze_month(monkeypatch, datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    with write_tx(db):
        quota.record_round_calls(db, settings, 2)
    with write_tx(db):
        quota.record_round_calls(db, settings, 2)

    assert _quota_row(db)["calls"] == 4
    assert _quota_row(db)["month"] == "2026-10"


def test_quota_counter_survives_restart_on_same_database(db, settings, monkeypatch):
    """Nova conexão sobre o mesmo banco continua de onde parou — nada de contador em memória."""
    _freeze_month(monkeypatch, datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    with write_tx(db):
        quota.record_round_calls(db, settings, 2)

    fresh = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        with write_tx(fresh):
            quota.record_round_calls(fresh, settings, 3)
        assert _quota_row(fresh)["calls"] == 5
    finally:
        fresh.close()


def test_quota_round_without_brapi_calls_creates_nothing(db, settings, monkeypatch):
    """Rodada sem chamada brapi (calls=0) não cria nem altera a linha no mês corrente."""
    _freeze_month(monkeypatch, datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    with write_tx(db):
        quota.record_round_calls(db, settings, 0)

    assert _quota_row(db) is None


def test_quota_utc_month_rollover_resets_calls_and_warned_80(db, settings, monkeypatch):
    """Virada do mês em UTC (D-06): reset de calls E warned_80 — relógio UTC, não local.

    2026-11-01T02:00Z = 31/10/2026 23:00 em Brasília: o mês UTC já virou e o
    local não — o reset prova que a comparação usa o mês UTC.
    """
    _freeze_month(monkeypatch, datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    with write_tx(db):
        quota.record_round_calls(db, settings, 500)
    # Linha gravada em mês anterior (injeção) com o aviso de 80% já dado:
    db.execute("UPDATE quota SET month = '2026-09', warned_80 = 1")

    _freeze_month(monkeypatch, datetime(2026, 11, 1, 2, 0, tzinfo=UTC))
    with write_tx(db):
        quota.record_round_calls(db, settings, 1)

    row = _quota_row(db)
    assert row["month"] == "2026-11"  # mês UTC novo (o fuso local ainda está em outubro)
    assert row["calls"] == 1  # zerado antes de somar
    assert row["warned_80"] == 0  # a flag também zera: o aviso volta no mês novo


def test_quota_warning_80_emitted_exactly_once(db, settings, monkeypatch, capsys):
    """limit=10: quota_warning_80 sai EXATAMENTE uma vez, no cruzamento de 8 (0.8×10)."""
    limit10 = replace(settings, brapi_monthly_limit=10)
    _freeze_month(monkeypatch, datetime(2026, 10, 28, 12, 0, tzinfo=UTC))

    with write_tx(db):
        quota.record_round_calls(db, limit10, 7)  # 7 < 8: ainda não
    assert "quota_warning_80" not in capsys.readouterr().out

    with write_tx(db):
        quota.record_round_calls(db, limit10, 1)  # 7+1=8 → cruza
    assert capsys.readouterr().out.count("quota_warning_80") == 1
    assert _quota_row(db)["warned_80"] == 1  # a flag impede repetição

    with write_tx(db):
        quota.record_round_calls(db, limit10, 2)  # rodada seguinte: NÃO repete
    assert "quota_warning_80" not in capsys.readouterr().out


# --- 02-03 Task 2 (D-05, SC-4): porta a 100%, transição única e desbloqueio ---


def test_b3_quota_allows_door_arithmetic(db, settings, monkeypatch):
    """Porta: gravado 8 de 10 deixa sair mais 2 (a que cruza ainda executa); gravado 10 fecha."""
    limit10 = replace(settings, brapi_monthly_limit=10)
    _freeze_month(monkeypatch, datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    assert quota.b3_quota_allows(db, limit10, 0) is True  # sem linha: porta aberta

    db.execute(
        "INSERT INTO quota (source, month, calls, warned_80) VALUES ('brapi', '2026-10', 8, 1)"
    )
    assert quota.b3_quota_allows(db, limit10, 0) is True  # 8 < 10
    assert quota.b3_quota_allows(db, limit10, 1) is True  # 9 < 10: a penúltima sai
    assert quota.b3_quota_allows(db, limit10, 2) is False  # 8+2 >= 10: porta fechada

    db.execute("UPDATE quota SET calls = 10")
    assert quota.b3_quota_allows(db, limit10, 0) is False  # 100%: nada mais sai


def test_quota_exhausted_emitted_once_at_crossing(db, settings, monkeypatch, capsys):
    """limit=10: quota_exhausted sai EXATAMENTE uma vez, quando calls cruza o limite."""
    limit10 = replace(settings, brapi_monthly_limit=10)
    _freeze_month(monkeypatch, datetime(2026, 10, 28, 12, 0, tzinfo=UTC))

    with write_tx(db):
        quota.record_round_calls(db, limit10, 9)  # 9 < 10: a porta ainda está aberta
    assert "quota_exhausted" not in capsys.readouterr().out

    with write_tx(db):
        quota.record_round_calls(db, limit10, 1)  # 9+1=10 → cruza
    assert capsys.readouterr().out.count("quota_exhausted") == 1

    with write_tx(db):
        quota.record_round_calls(db, limit10, 0)  # porta fechada: nenhuma chamada nova sai
    assert "quota_exhausted" not in capsys.readouterr().out  # transição derivada: não repete


def test_quota_unlocks_after_utc_rollover(db, settings, monkeypatch):
    """Porta fechada + virada UTC: a primeira rodada do mês novo destrava (D-06)."""
    limit10 = replace(settings, brapi_monthly_limit=10)
    _freeze_month(monkeypatch, datetime(2026, 10, 28, 12, 0, tzinfo=UTC))
    with write_tx(db):
        quota.record_round_calls(db, limit10, 10)
    assert quota.b3_quota_allows(db, limit10, 0) is False  # fechada a 100%

    _freeze_month(monkeypatch, datetime(2026, 11, 1, 2, 0, tzinfo=UTC))
    # Primeira rodada do mês novo — mesmo SEM chamada (pregão fechado), o reset acontece:
    with write_tx(db):
        quota.record_round_calls(db, limit10, 0)
    row = _quota_row(db)
    assert row["month"] == "2026-11"
    assert row["calls"] == 0
    assert row["warned_80"] == 0
    assert quota.b3_quota_allows(db, limit10, 0) is True  # destravada já na primeira rodada
