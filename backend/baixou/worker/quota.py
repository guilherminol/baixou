"""Porta e registro da cota mensal da brapi (POLL-03, D-05, D-06, SC-4).

A linha vive na tabela ``quota`` do esquema 0001 (source="brapi",
month="YYYY-MM" UTC, calls, warned_80) — os primeiros escritores são esta
fase; nenhuma migração nova. Regras (arquitetura §Agendador, regra da Cota):

- Cada ``fetch_one`` executado soma 1; chamada bloqueada pela porta, pela
  janela de pregão ou pelo divisor de cadência não soma — quem decide é a
  rodada em worker.main; aqui está só a aritmética.
- A 80% do limite: o evento ``quota_warning_80`` sai EXATAMENTE uma vez e a
  flag ``warned_80`` impede repetição (D-05).
- A 100%: a porta (``b3_quota_allows``) fecha para a B3; cripto nunca é
  bloqueada por cota da brapi. O evento ``quota_exhausted`` é transição
  derivada (calls estava abaixo e ficou >= limite): com a porta fechada
  nenhuma chamada sai e o evento não repete naturalmente — sem flag extra.
- A virada do mês é em UTC (D-06): comparar o mês UTC corrente com a linha
  gravada reseta ``calls`` e ``warned_80`` dentro do write_tx da rodada.

Leitura fora de transação (disciplina WAL); escrita SOMENTE dentro do
write_tx único da rodada, via ``record_round_calls``. Estilo de
core/migrations.py: funções de módulo, constantes em MAIÚSCULAS.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from baixou.core.config import Settings

_SOURCE = "brapi"
_MONTH_FORMAT = "%Y-%m"


def current_month() -> str:
    """Mês UTC corrente no formato "YYYY-MM" (D-06) — nunca o fuso local."""
    return datetime.now(UTC).strftime(_MONTH_FORMAT)


def read_quota(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Lê a linha de cota da brapi (leitura fora de transação — disciplina WAL)."""
    return None  # RED (TDD): leitura ainda não implementada


def b3_quota_allows(
    conn: sqlite3.Connection, settings: Settings, calls_this_round: int = 0
) -> bool:
    """Porta (T-02-09): a próxima chamada brapi da rodada pode sair?

    Compara as chamadas gravadas mais as chamadas já feitas NESTA rodada com
    ``settings.brapi_monthly_limit``. A chamada que CRUZA o limite ainda
    executa e conta (a checagem é ANTES de cada fetch_one).
    """
    return True  # RED (TDD): porta sempre aberta no stub


def record_round_calls(conn: sqlite3.Connection, settings: Settings, calls_made: int) -> None:
    """Registro da rodada — chamar DENTRO do write_tx único (T-02-01).

    Soma ``calls_made`` (chamadas efetuadas; bloqueadas não chegam aqui),
    detecta a virada do mês UTC comparando o mês corrente com a linha gravada
    (reset de calls/warned_80 — D-06) e calcula as transições de log.
    """
    return None  # RED (TDD): o registro ainda não grava nada
