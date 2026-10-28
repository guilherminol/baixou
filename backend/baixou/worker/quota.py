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
from baixou.core.log import log

_SOURCE = "brapi"
_MONTH_FORMAT = "%Y-%m"


def current_month() -> str:
    """Mês UTC corrente no formato "YYYY-MM" (D-06) — nunca o fuso local."""
    return datetime.now(UTC).strftime(_MONTH_FORMAT)


def read_quota(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Lê a linha de cota da brapi (leitura fora de transação — disciplina WAL)."""
    return conn.execute(
        "SELECT month, calls, warned_80 FROM quota WHERE source = ?",
        (_SOURCE,),
    ).fetchone()


def b3_quota_allows(
    conn: sqlite3.Connection, settings: Settings, calls_this_round: int = 0
) -> bool:
    """Porta (T-02-09): a próxima chamada brapi da rodada pode sair?

    Compara as chamadas gravadas do mês corrente mais as chamadas já feitas
    NESTA rodada com ``settings.brapi_monthly_limit``. A chamada que CRUZA o
    limite ainda executa e conta (a checagem é ANTES de cada fetch_one):
    gravado 9 de 10 → a próxima sai e fecha a porta. Sem linha, ou com a
    linha ainda no mês anterior (a virada ainda não foi processada), a porta
    abre: o reset da virada (D-06) destrava a B3 já na primeira rodada do
    mês novo.
    """
    row = read_quota(conn)
    if row is None or row["month"] != current_month():
        recorded = 0  # sem linha ou mês anterior: a virada destrava (D-06)
    else:
        recorded = row["calls"]
    return recorded + calls_this_round < settings.brapi_monthly_limit


def record_round_calls(conn: sqlite3.Connection, settings: Settings, calls_made: int) -> None:
    """Registro da rodada — chamar DENTRO do write_tx único (T-02-01).

    Soma ``calls_made`` (chamadas efetuadas; bloqueadas não chegam aqui),
    detecta a virada do mês UTC comparando o mês corrente com a linha gravada
    (reset de calls/warned_80 — D-06) e calcula as transições: warned_80
    0→1 emite ``quota_warning_80`` exatamente uma vez; cruzar o limite emite
    ``quota_exhausted`` exatamente uma vez (transição derivada — com a porta
    fechada nas rodadas seguintes nenhuma chamada sai e o evento não repete;
    sem flag extra no esquema). Log é stdout, não I/O de banco: as transições
    podem ser emitidas dentro da transação. Rodada sem chamada nenhuma no
    mês corrente não escreve nem emite evento; com virada a processar, o
    reset acontece mesmo assim (calls=0 no mês novo).
    """
    month = current_month()
    row = read_quota(conn)
    if row is None:
        base_calls, warned_80 = 0, 0
    elif row["month"] != month:  # virada do mês UTC (D-06): zera e destrava
        base_calls, warned_80 = 0, 0
    else:
        base_calls, warned_80 = row["calls"], row["warned_80"]

    if calls_made == 0 and (row is None or row["month"] == month):
        return  # nada a somar no mês corrente: nenhuma escrita, nenhum evento

    limit = settings.brapi_monthly_limit
    new_calls = base_calls + calls_made
    new_warned = warned_80
    if calls_made and not warned_80 and new_calls * 5 >= 4 * limit:  # 80% exato em inteiros
        new_warned = 1
        log(
            "warning",
            "source",
            "quota_warning_80",
            calls=new_calls,
            month=month,
            text="80% da cota mensal da brapi consumida",
        )
    if calls_made and base_calls < limit <= new_calls:  # cruzou 100%: transição única
        log(
            "warning",
            "source",
            "quota_exhausted",
            calls=new_calls,
            month=month,
            text="cota mensal da brapi esgotada — a B3 não é consultada até virar o mês",
        )
    conn.execute(
        """
        INSERT INTO quota (source, month, calls, warned_80)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (source) DO UPDATE SET
            month = excluded.month,
            calls = excluded.calls,
            warned_80 = excluded.warned_80
        """,
        (_SOURCE, month, new_calls, new_warned),
    )
