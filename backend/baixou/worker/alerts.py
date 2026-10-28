"""Máquina de estados do alerta: cruzamento, entrega multi-canal e gravação (SC-1, SC-3).

Regras desta fase (arquitetura §Máquina de estados do alerta; D-12/D-15/D-16):

- Preço ≤ alvo com o item aguardando (alerted=0, borda INCLUSIVA — D-12)
  envia o aviso por TODOS os canais configurados, EM SEQUÊNCIA e FORA de
  qualquer transação (disciplina WAL T-02-01 — a rede nunca segura o lock).
- A regra "o aviso conta como enviado se pelo menos um canal aceitar" fica no
  worker (arquitetura §Interfaces de notificadores): com aceite de qualquer
  canal (HTTP 2xx — D-16), o item fica alertado — numa write_tx curta PRÓPRIA
  grava alerted=1, alerted_at (hora UTC do disparo), alerted_target e
  notify_state; com TODOS recusando, a MESMA write_tx grava SOMENTE
  notify_state — o item segue aguardando (alerted permanece 0) e a rodada
  seguinte tenta de novo, alcançando inclusive canais configurados depois do
  início da tentativa.
- notify_state é gravado em TODA tentativa de envio — aceito ou não — como
  JSON por canal no formato final consumido pelas fases 4–5 (D-15), com as
  chaves na ordem de ``CHANNEL_ORDER`` e valores ``"ok"`` ou
  ``"erro <kind> (HTTP <n>)"``. NÃO há reenvio seletivo ao canal que falhou:
  o próximo cruzamento reenvia por todos (arquitetura).
- Item com NENHUM canal configurado não gera envio nem escrita (lista vazia —
  avisos desligados, D-08).
- Preço ≤ alvo com o item JÁ alertado não repete o aviso — um aviso por
  cruzamento. Sem cotação (price ou price_at ausentes — estado "none") o item
  nunca é avaliado.
- Rearme por margem, troca de alvo e frescor por idade (estados
  ok/stale/closed) são das Tasks 2/3 deste plano — a estrutura do loop já os
  acomoda (a precedência por item entra em ordem).

Eventos: ``alert_sent`` (uma vez por cruzamento entregue) e
``alert_delivery_failed`` por canal recusado — citam kind e código HTTP,
nunca URL, token, tópico ou chat_id (K2/T-03-01/T-03-02). Estilo de
worker/quota.py: funções de módulo, leitura fora de transação, escrita só em
write_tx do chamador (aqui: write_tx própria do resultado do alerta).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime

import httpx

from baixou.core.config import Settings
from baixou.core.db import write_tx
from baixou.core.log import log
from baixou.worker import messages
from baixou.worker.notifiers.base import CHANNEL_ORDER, NotificationResult, configured_channels

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _notify_state(results: dict[str, NotificationResult]) -> str:
    """JSON por canal (D-15): {"canal": "ok" | "erro <kind> (HTTP <n>"} na ordem de CHANNEL_ORDER.

    Toda tentativa entra — aceita ou recusada — porque o resultado por canal
    é o que as fases 4–5 consomem (formato congelado, D-15).
    """
    state: dict[str, str] = {}
    for channel in CHANNEL_ORDER:
        result = results.get(channel)
        if result is None:
            continue
        if result.ok:
            state[channel] = "ok"
        elif result.http_status is not None:
            state[channel] = f"erro {result.kind} (HTTP {result.http_status})"
        else:
            state[channel] = f"erro {result.kind}"
    return json.dumps(state, ensure_ascii=False)


def _deliver(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    settings: Settings,
    client: httpx.Client,
    now_utc: datetime,
    channels: list[tuple[str, Callable]],
) -> None:
    """Entrega o aviso de um cruzamento por todos os canais e grava o resultado (SC-3).

    Rede FORA de transação (T-02-01); o resultado — aceito ou não — vai em
    write_tx curta própria. Regra "≥1 canal aceitou": com qualquer aceite o
    item fica alertado; com todos recusando só ``notify_state`` é gravado e a
    rodada seguinte tenta de novo. Sem canal configurado não há envio nem
    escrita (lista vazia — tratado no chamador).
    """
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
    results: dict[str, NotificationResult] = {}
    for channel, send in channels:
        result = send(client, settings, title, body)  # rede FORA de transação (T-02-01)
        results[channel] = result
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

    accepted = [channel for channel in CHANNEL_ORDER if results.get(channel) is not None and results[channel].ok]
    with write_tx(conn):  # write_tx curta PRÓPRIA do resultado (T-02-01)
        if accepted:
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
        else:
            # Todos recusaram: SOMENTE notify_state — o item segue aguardando
            # (alerted permanece 0) e a rodada seguinte tenta de novo (SC-3).
            conn.execute(
                "UPDATE quote SET notify_state = ? WHERE symbol = ? AND market = ?",
                (_notify_state(results), symbol, row["market"]),
            )
    if accepted:
        log(
            "info",
            "alert",
            "alert_sent",
            symbol=symbol,
            channels=accepted,
            text=f"aviso de preço enviado: {symbol}",
        )


def process_alerts(
    conn: sqlite3.Connection, settings: Settings, client: httpx.Client, now_utc: datetime
) -> None:
    """Avalia cada item, entrega os avisos devidos e grava o resultado (SC-1/SC-3).

    Chamar APÓS a write_tx das cotações da rodada: lê ``watch`` LEFT JOIN
    ``quote`` fora de transação (ordem determinística symbol, market), envia
    pelos canais fora de transação (rede nunca dentro de write_tx — T-02-01)
    e grava o resultado dos itens cruzados em write_tx curta própria.
    ``now_utc`` (aware) vira o ``alerted_at`` no formato de timestamp do banco.
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
        if not channels:
            continue  # avisos desligados: nenhum envio nem escrita (D-08)
        _deliver(conn, row, settings, client, now_utc, channels)
