"""Moeda do item derivada do market/sufixo (FR-16, D-02) — núcleo comum stdlib-puro.

Movida de ``worker/messages.py`` no plano 04-02 (WATCH-02): a moeda é a
MESMA função do aviso e da API — uma única fonte de verdade na fronteira
entre os dois serviços. Módulo 100% stdlib, sem I/O: o worker (sem FastAPI/
pydantic) e o web podem importá-lo sem puxar um ao outro — a fronteira
exige moeda FORA dos dois (D-02; molde sources/base.py).
"""

from __future__ import annotations

_MARKET_B3 = "b3"
_BRL_SUFFIX = "BRL"
_CURRENCY_BRL = "R$"
_CURRENCY_USD = "US$"


def currency_for(symbol: str, market: str) -> str:
    """Moeda do item (D-02): derivada do market e do sufixo do símbolo (FR-16).

    B3 é em reais; no mercado cripto o sufixo manda — BRL → R$;
    USDT/USDC/USD e sufixo desconhecido → US$, o padrão do mercado cripto.
    """
    if market == _MARKET_B3:
        return _CURRENCY_BRL
    if symbol.endswith(_BRL_SUFFIX):
        return _CURRENCY_BRL
    return _CURRENCY_USD
