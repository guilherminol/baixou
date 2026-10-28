"""Montagem das mensagens de aviso: título estável e corpo PT-BR (D-01, D-03).

O título é obrigatório e estável (arquitetura §Interfaces de fontes e
notificadores) — "Baixou: <SÍMBOLO>" (D-03): o nome do projeto é o próprio
evento; os números ficam no corpo. O corpo mínimo desta fase leva preço
atual, alvo e a hora da cotação convertida para o fuso do pregão (D-01 na
forma fina); moeda, variação do dia e nota de suspeita chegam no plano 03-02,
que amplia a assinatura. A mensagem fica restrita a dados do item — nunca
linguagem de compra/venda (o aviso não é recomendação). Horários ficam em UTC
no banco; conversão para fuso só aqui, na saída.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def alert_title(symbol: str) -> str:
    """Título estável do aviso de preço (D-03) — idêntico em todos os canais."""
    return f"Baixou: {symbol}"


def price_alert_body(price: float, target_below: float, price_at: str, tz_name: str) -> str:
    """Corpo mínimo do aviso: preço, alvo e hora da cotação no fuso (D-01).

    ``price_at`` chega no formato de timestamp do banco (UTC ISO-8601) e é
    convertido para ``tz_name`` só aqui. Timestamp ilegível (nunca esperado —
    o gravador é nosso) aparece cru em vez de derrubar a rodada.
    """
    try:
        quoted_at = datetime.fromisoformat(price_at.replace("Z", "+00:00"))
        hora = quoted_at.astimezone(ZoneInfo(tz_name)).strftime("%d/%m/%Y %H:%M")
    except ValueError, ZoneInfoNotFoundError:
        hora = price_at
    return f"Preço atual: {price:.2f}\nAlvo: {target_below:.2f}\nCotação de: {hora} ({tz_name})"
