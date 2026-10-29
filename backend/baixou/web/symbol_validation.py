"""Validação de símbolo na fonte antes de gravar (D-03/D-04) — híbrido por mercado.

Cripto valida SÍNCRONO na Binance (barato, sem cota — o lote único de 1
símbolo); B3 valida na brapi SOMENTE em pregão — fora dele o cadastro é
aceito sem rede e a 1ª rodada valida de fato (a brapi não responde fechada;
recusar cadastro de B3 à noite bloquearia o produto). Recusa por símbolo
inexistente é ``SymbolNotFoundError`` (422 na rota); qualquer outra falha de
fonte (rede, 5xx, limite, formato) vira ``SourceUnavailableError`` (503
"tente de novo") — o admin precisa saber que o símbolo NÃO foi verificado
(D-04: recusa por inexistência e falha de rede nunca se disfarçam uma de
outra). As mensagens citam só o kind (K2) e as exceções httpx não são
encadeadas (``from None`` — a URL da brapi leva o token na query e nunca
pode escapar por um traceback). A validação NÃO grava cotação e NÃO conta
na cota da brapi (limite registrado no COVERAGE.md da fase). Importar de
``baixou.worker.sources`` é permitido — o proibido é o REVERSO (worker →
web, TID251); as fontes são stdlib+httpx puros.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from baixou.core.config import Settings
from baixou.worker.sources import binance, brapi
from baixou.worker.sources.base import (
    SourceError,
    SourceUnavailableError,
    SymbolNotFoundError,
    b3_market_open,
)

_TIMEOUT_S = 10.0


def novo_cliente() -> httpx.Client:
    """Cliente HTTP da validação com timeout curto de 10 s (D-04)."""
    return httpx.Client(timeout=_TIMEOUT_S)


def validar(client: httpx.Client, settings: Settings, symbol: str, market: str) -> None:
    """Verifica o símbolo na fonte: None = aceito; exceção tipada = recusa/status.

    ``binance.fetch_batch`` devolve o ruim por símbolo no dict de erros e
    levanta falha de NÍVEL da fonte — ambos mapeados aqui para o par
    422/503. A checagem de pregão usa o relógio do módulo (injetável nos
    testes com ``monkeypatch``, molde test_round.py).
    """
    if market == "crypto":
        try:
            _acertos, erros = binance.fetch_batch(client, [symbol])
        except SourceError as exc:
            raise SourceUnavailableError(
                f"fonte indisponível para validar o símbolo: {exc.kind}", symbol=symbol
            ) from None
        erro = erros.get(symbol)
        if erro is None:
            return
        if erro.kind == "not_found":
            raise SymbolNotFoundError(f"símbolo não encontrado na Binance: {symbol}", symbol=symbol)
        raise SourceUnavailableError(
            f"fonte indisponível para validar o símbolo: {erro.kind}", symbol=symbol
        )
    if not b3_market_open(datetime.now(UTC), settings.tz_market):
        return  # fora do pregão: aceito sem rede — a 1ª rodada valida (D-03)
    try:
        brapi.fetch_one(client, symbol, settings.brapi_token)
    except SymbolNotFoundError:
        raise  # recusa real: 422 nomeando o ticker (D-04 separa do 503)
    except SourceError as exc:
        raise SourceUnavailableError(
            f"fonte indisponível para validar o símbolo: {exc.kind}", symbol=symbol
        ) from None
