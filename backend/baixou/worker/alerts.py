"""Máquina de estados do alerta: cruzamento, rearme, troca de alvo e entrega (SC-1, SC-2, SC-3).

Precedência por item a cada rodada (arquitetura §Máquina de estados do
alerta; D-12/D-13/D-15/D-16):

- (1) Item alertado com ``alerted_target`` DIFERENTE de ``watch.target_below``
  (incluindo NULL) é TROCA DE ALVO (D-13): write_tx zera alerted=0 e
  alerted_target=NULL mantendo alerted_at, e NADA é enviado nesta rodada —
  a reavaliação é na rodada seguinte (o web não precisa gravar nas cotações).
- (2) Item alertado, mesmo alvo, com preço ≥ alvo × (1 + REARM_PCT ÷ 100) —
  borda INCLUSIVA na margem exata — é REARME (D-12): mesma write_tx de zera
  estado, mantendo alerted_at, e NENHUMA mensagem sai (rearme é mudança de
  estado, não aviso).
- (3) Item alertado abaixo da margem: nada — não repete o aviso (um aviso por
  cruzamento, SC-2).
- (4) Item aguardando (alerted=0) com preço ≤ alvo (borda INCLUSIVA — D-12)
  dispara a entrega: o aviso vai por TODOS os canais configurados, EM
  SEQUÊNCIA e FORA de qualquer transação (disciplina WAL T-02-01 — a rede
  nunca segura o lock).

Regra "o aviso conta como enviado se pelo menos um canal aceitar" fica no
worker (arquitetura §Interfaces de notificadores): com aceite de qualquer
canal (HTTP 2xx — D-16), o item fica alertado — numa write_tx curta PRÓPRIA
grava alerted=1, alerted_at (hora UTC do disparo), alerted_target e
notify_state; com TODOS recusando, a MESMA write_tx grava SOMENTE
notify_state — o item segue aguardando e a rodada seguinte tenta de novo,
alcançando inclusive canais configurados depois do início da tentativa.

notify_state é gravado em TODA tentativa de envio — aceito ou não — como
JSON por canal no formato final consumido pelas fases 4–5 (D-15), com as
chaves na ordem de ``CHANNEL_ORDER`` e valores ``"ok"`` ou
``"erro <kind> (HTTP <n>)"``. NÃO há reenvio seletivo ao canal que falhou:
o próximo cruzamento reenvia por todos (arquitetura).

Item com NENHUM canal configurado não gera envio nem escrita de entrega
(lista vazia — avisos desligados, D-08); rearme e troca de alvo independem de
canal (mudança de estado do item). Sem cotação (price ou price_at ausentes —
estado "none") o item nunca é avaliado. ``alerted_at`` é histórico — "hora do
último disparo" (ALERT-04) — e SOBREVIVE a rearme e a troca de alvo; após o
rearme, um novo cruzamento para baixo dispara NOVO aviso e grava novo
alerted_at/alerted_target. O gate de frescor por idade (ok/stale/closed/none)
entra na Task 3 deste plano.

Eventos: ``alert_sent`` (uma vez por cruzamento entregue),
``alert_delivery_failed`` por canal recusado, ``alert_rearmed`` e
``alert_target_changed`` (mudanças de estado sem envio) — citam kind e código
HTTP quando falha de canal, nunca URL, token, tópico ou chat_id
(K2/T-03-01/T-03-02). Estilo de worker/quota.py: funções de módulo, leitura
fora de transação, escrita só em write_tx do chamador (aqui: write_tx própria
do resultado do alerta).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal

import httpx

from baixou.core.config import Settings
from baixou.core.db import write_tx
from baixou.core.log import log
from baixou.worker import messages
from baixou.worker.notifiers.base import CHANNEL_ORDER, NotificationResult, configured_channels

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _rearm_threshold(target: float, rearm_pct: int) -> Decimal:
    """Margem de rearme ``alvo × (1 + REARM_PCT ÷ 100)`` em Decimal exato (D-12).

    A borda é INCLUSIVA na margem exata — e em float a conta não fecha:
    ``15.0 * 1.01`` sai ACIMA do literal ``15.15`` e o rearme nunca dispararia
    na borda (mesma armadilha do ``0.8 * 15000`` da cota de 80%, precedente da
    Fase 2). Decimal sobre ``str`` fecha a conta exata para valores com
    representação decimal finita — o que fontes e seeds produzem.
    """
    return Decimal(str(target)) * (100 + rearm_pct) / 100


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


def _reset_alert_state(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    """Volta o item a aguardando mantendo ``alerted_at`` como histórico (ALERT-04).

    Escrita em write_tx curta própria (T-02-01): zera ``alerted`` e
    ``alerted_target``; ``alerted_at`` e ``notify_state`` ficam intactos —
    rearme e troca de alvo são mudança de estado, nunca entrega.
    """
    with write_tx(conn):
        conn.execute(
            "UPDATE quote SET alerted = 0, alerted_target = NULL "
            "WHERE symbol = ? AND market = ?",
            (row["symbol"], row["market"]),
        )


def _evaluate_alerted(conn: sqlite3.Connection, row: sqlite3.Row, settings: Settings) -> None:
    """Precedência sobre o item alertado: troca de alvo → rearme → nada (D-13/D-12).

    (1) ``alerted_target`` DIFERENTE de ``watch.target_below`` (incluindo NULL)
    é TROCA DE ALVO: zera o estado e reavalia na RODADA SEGUINTE — nada é
    enviado nesta rodada (o web não precisa gravar nas cotações). (2) Mesmo
    alvo com preço ≥ alvo × (1 + REARM_PCT ÷ 100) — borda INCLUSIVA na margem
    exata, em Decimal (mesma armadilha float do ``0.8 × 15000`` da cota de 80%,
    precedente da Fase 2) — é REARME, sem mensagem. (3) Abaixo da margem: nada
    — um aviso por cruzamento (SC-2). Nenhuma mensagem sai daqui; a comparação
    do alvo é contra as linhas persistidas lidas nesta rodada, nunca contra
    memória entre rodadas (T-03-05).
    """
    if row["alerted_target"] is None or row["alerted_target"] != row["target_below"]:
        _reset_alert_state(conn, row)
        log(
            "info",
            "alert",
            "alert_target_changed",
            symbol=row["symbol"],
            text=f"alvo alterado: {row['symbol']} volta a aguardar (reavalia na rodada seguinte)",
        )
        return
    price = Decimal(str(row["price"]))
    if price >= _rearm_threshold(row["target_below"], settings.rearm_pct):
        _reset_alert_state(conn, row)
        log(
            "info",
            "alert",
            "alert_rearmed",
            symbol=row["symbol"],
            text=f"rearme pela margem: {row['symbol']} volta a aguardar",
        )


def process_alerts(
    conn: sqlite3.Connection, settings: Settings, client: httpx.Client, now_utc: datetime
) -> None:
    """Avalia cada item na precedência troca de alvo → rearme → cruzamento (SC-1/SC-2/SC-3).

    Chamar APÓS a write_tx das cotações da rodada: lê ``watch`` LEFT JOIN
    ``quote`` fora de transação (ordem determinística symbol, market), aplica a
    precedência por item e envia pelos canais fora de transação (rede nunca
    dentro de write_tx — T-02-01), gravando cada resultado em write_tx curta
    própria. Item alertado passa por ``_evaluate_alerted`` — troca de alvo e
    rearme mudam estado sem mensagem, com ``alerted_at`` preservado; item
    aguardando com preço ≤ alvo (borda inclusiva) dispara a entrega multi-canal.
    ``now_utc`` (aware) vira o ``alerted_at`` no formato de timestamp do banco.
    """
    rows = conn.execute(
        """
        SELECT w.symbol AS symbol, w.market AS market, w.target_below AS target_below,
               q.price AS price, q.price_at AS price_at, q.change_pct AS change_pct,
               q.alerted AS alerted, q.alerted_target AS alerted_target
        FROM watch w
        LEFT JOIN quote q ON q.symbol = w.symbol AND q.market = w.market
        ORDER BY w.symbol, w.market
        """
    ).fetchall()

    channels = configured_channels(settings)
    for row in rows:
        if row["price"] is None or row["price_at"] is None:
            continue  # estado "none": sem cotação gravada, nunca avalia
        if row["alerted"]:
            _evaluate_alerted(conn, row, settings)  # troca de alvo → rearme → nada
            continue
        if row["price"] > row["target_below"]:
            continue  # aguardando acima do alvo: não cruza (borda ≤ é inclusiva — D-12)
        if not channels:
            continue  # avisos desligados: nenhum envio nem escrita (D-08)
        _deliver(conn, row, settings, client, now_utc, channels)
