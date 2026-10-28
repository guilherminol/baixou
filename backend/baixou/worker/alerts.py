"""Máquina de estados do alerta: cruzamento, entrega e gravação do resultado (SC-1).

Regras desta fase (arquitetura §Máquina de estados do alerta; D-12/D-15/D-16):

- Preço ≤ alvo com o item aguardando (alerted=0, borda INCLUSIVA — D-12)
  envia o aviso por todos os canais configurados, EM SEQUÊNCIA e FORA de
  qualquer transação (disciplina WAL T-02-01 — a rede nunca segura o lock).
- Se ao menos um canal aceitar (HTTP 2xx — D-16), o item fica alertado: numa
  write_tx curta PRÓPRIA grava alerted=1, alerted_at (hora UTC do disparo),
  alerted_target e notify_state — JSON por canal no formato final consumido
  pelas fases 4–5 (D-15), com as chaves na ordem de envio.
- Se nenhum canal aceitar, NADA é gravado: o item segue aguardando e a rodada
  seguinte tenta de novo (o notify_state de falha por canal chega no 03-03).
- Preço ≤ alvo com o item JÁ alertado não repete o aviso — um aviso por
  cruzamento. Sem cotação (price ou price_at ausentes — estado "none") o item
  nunca é avaliado.
- Rearme por margem, troca de alvo, frescor por idade e estados stale/closed
  são dos planos 03-03/03-04 — não moram aqui.

Eventos: ``alert_sent`` (uma vez por cruzamento entregue) e
``alert_delivery_failed`` por canal recusado — citam kind e código HTTP,
nunca URL, token ou chat_id (K2/T-03-01). Estilo de worker/quota.py: funções
de módulo, leitura fora de transação, escrita só em write_tx do chamador
(aqui: write_tx própria do resultado do alerta).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import httpx

from baixou.core.config import Settings
from baixou.core.db import write_tx
from baixou.core.log import log
from baixou.worker import messages
from baixou.worker.notifiers.base import NotificationResult, configured_channels

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _notify_state(results: list[tuple[str, NotificationResult]]) -> str:
    """JSON por canal (D-15): {"canal": "ok" | "erro <kind> (HTTP <n>)"} na ordem de envio."""
    state: dict[str, str] = {}
    for channel, result in results:
        if result.ok:
            state[channel] = "ok"
        elif result.http_status is not None:
            state[channel] = f"erro {result.kind} (HTTP {result.http_status})"
        else:
            state[channel] = f"erro {result.kind}"
    return json.dumps(state, ensure_ascii=False)


def process_alerts(
    conn: sqlite3.Connection, settings: Settings, client: httpx.Client, now_utc: datetime
) -> None:
    """Avalia cada item, entrega os avisos devidos e grava o resultado (SC-1).

    Chamar APÓS a write_tx das cotações da rodada: lê ``watch`` LEFT JOIN
    ``quote`` fora de transação, envia pelos canais fora de transação (rede
    nunca dentro de write_tx — T-02-01) e grava o resultado dos itens cruzados
    em write_tx curta própria. ``now_utc`` (aware) vira o ``alerted_at`` no
    formato de timestamp do banco.
    """
    rows = conn.execute(
        """
        SELECT w.symbol AS symbol, w.market AS market, w.target_below AS target_below,
               q.price AS price, q.price_at AS price_at, q.change_pct AS change_pct,
               q.alerted AS alerted
        FROM watch w
        LEFT JOIN quote q ON q.symbol = w.symbol AND q.market = w.market
        ORDER BY w.symbol, w.market
        """
    ).fetchall()

    channels = configured_channels(settings)
    for row in rows:
        if row["price"] is None or row["price_at"] is None:
            continue  # estado "none": sem cotação gravada, nunca avalia
        if row["alerted"] or row["price"] > row["target_below"]:
            continue  # já alertado (não repete) ou ainda acima do alvo (não cruza)

        symbol = row["symbol"]
        title = messages.alert_title(symbol)
        body = messages.price_alert_body(
            symbol,
            row["market"],
            row["price"],
            row["target_below"],
            row["change_pct"],
            row["price_at"],
            settings,
        )
        results: list[tuple[str, NotificationResult]] = []
        for channel, send in channels:
            result = send(client, settings, title, body)  # rede FORA de transação (T-02-01)
            results.append((channel, result))
            if not result.ok:
                log(
                    "warning",
                    "alert",
                    "alert_delivery_failed",
                    channel=channel,
                    kind=result.kind,
                    http_status=result.http_status,
                    text=f"falha de entrega no canal {channel}: {result.kind}",
                )
        if not any(result.ok for _channel, result in results):
            continue  # nenhum canal aceitou: nada gravado, a rodada seguinte tenta de novo

        with write_tx(conn):  # write_tx curta PRÓPRIA do resultado (T-02-01)
            conn.execute(
                """
                UPDATE quote
                SET alerted = 1, alerted_at = ?, alerted_target = ?, notify_state = ?
                WHERE symbol = ? AND market = ?
                """,
                (
                    now_utc.strftime(_TS_FORMAT),
                    row["target_below"],
                    _notify_state(results),
                    symbol,
                    row["market"],
                ),
            )
        log(
            "info",
            "alert",
            "alert_sent",
            symbol=symbol,
            channels=[channel for channel, result in results if result.ok],
            text=f"aviso de preço enviado: {symbol}",
        )
