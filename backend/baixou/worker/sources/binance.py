"""Fonte Binance (cripto): um lote único por rodada (POLL-01, POLL-02).

Endereços fixos no código (nunca de input — SSRF). A API exige a lista de
símbolos serializada em JSON na query (``symbols=["A","B"]``) e o lote é
all-or-nothing: um símbolo inválido derruba o lote inteiro com 400 ``-1121``
(Pitfall 2). Nesse caso — e só nesse, nunca retry cego de 400 — o módulo
repete 1-a-1 UMA vez com ``symbol=`` individual (shape objeto) e devolve o
ruim como ``SymbolNotFoundError`` para a rodada gravar em
``quote.last_fetch_error``; a quarentena das rodadas seguintes é estado
DERIVADO dessa coluna no banco (D-02 — sem tabela nova e sem estado em
memória). Outros 400 viram ``UnexpectedResponseError``; 429/418 viram
``RateLimitError`` sem retry em laço (limite é por IP — insistir bane de
2 min a 3 dias; Pitfall 2).

Espelho 451 (D-04): 451 ou erro de transporte no principal troca para o
espelho oficial de dados públicos ``data-api.binance.vision`` e PERMANECE
nele, com re-probe do principal 1× por hora — voltando a responder, retorna
ao principal. Cada transição emite UM evento de log (``mirror_fallback`` /
``mirror_recovery``); sucesso no espelho conta como sucesso da fonte. O
estado do espelho é SÓ em memória (reinício volta a tentar o principal —
decisão consciente do autor: nada de coluna/tabela para endpoint ativo).
O endpoint não devolve hora da cotação nem variação: ``price_at`` é o
instante da resposta (UTC) e ``change_pct`` é None.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from baixou.core.log import log
from baixou.worker.sources.base import (
    QuoteResult,
    RateLimitError,
    SourceError,
    SourceUnavailableError,
    SymbolNotFoundError,
    UnexpectedResponseError,
)

_PRIMARY_BASE = "https://api.binance.com"
_MIRROR_BASE = "https://data-api.binance.vision"
_TICKER_PATH = "/api/v3/ticker/price"
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_INVALID_SYMBOL_CODE = -1121  # o único 400 que autoriza o 1-a-1 único (Pitfall 2)
_RATE_LIMIT_STATUS = frozenset({429, 418})  # 418 = ban automático de IP

_MIRROR_REPROBE_S = 3600.0  # re-probe do principal 1× por hora (D-04)


@dataclass
class _MirrorState:
    """Estado do espelho SÓ em memória (D-04): reinício volta a tentar o principal."""

    using_mirror: bool = False
    last_primary_probe_at: datetime | None = None


_mirror_state = _MirrorState()


def _now() -> datetime:
    """Relógio da fonte (injetável nos testes com ``monkeypatch``)."""
    return datetime.now(UTC)


def _switch_to_mirror(now: datetime) -> None:
    """Troca principal → espelho emitindo ``mirror_fallback`` UMA vez (D-04)."""
    _mirror_state.using_mirror = True
    _mirror_state.last_primary_probe_at = now
    log(
        "info",
        "source",
        "mirror_fallback",
        text=(
            "bloqueio no endereço principal da Binance; "
            "usando o espelho de dados públicos data-api.binance.vision"
        ),
    )


def _switch_to_primary() -> None:
    """Volta espelho → principal emitindo ``mirror_recovery`` UMA vez (D-04)."""
    _mirror_state.using_mirror = False
    _mirror_state.last_primary_probe_at = None
    log(
        "info",
        "source",
        "mirror_recovery",
        text="endereço principal da Binance voltou a responder; espelho desativado",
    )


def _active_base(now: datetime) -> tuple[str, bool]:
    """Devolve ``(base ativa, é re-probe?)``: no espelho, testa o principal a cada hora."""
    if not _mirror_state.using_mirror:
        return _PRIMARY_BASE, False
    probed_at = _mirror_state.last_primary_probe_at
    if probed_at is None or (now - probed_at).total_seconds() >= _MIRROR_REPROBE_S:
        return _PRIMARY_BASE, True  # re-probe horário do principal
    return _MIRROR_BASE, False


def _parse_tickers(payload: object) -> dict[str, QuoteResult]:
    """Parseia o shape array (resposta de ``symbols``) e o objeto (``symbol=`` único)."""
    if isinstance(payload, dict):
        entries = [payload]  # shape objeto: resposta de `symbol` único
    elif isinstance(payload, list):
        entries = payload  # shape array: resposta de `symbols` (caminho feliz do lote)
    else:
        raise UnexpectedResponseError("shape inesperado do lote da Binance")
    price_at = datetime.now(UTC).strftime(_TS_FORMAT)
    results: dict[str, QuoteResult] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise UnexpectedResponseError("entrada do lote sem shape de ticker")
        symbol = entry.get("symbol")
        raw_price = entry.get("price")
        price_ok = isinstance(raw_price, (str, int, float)) and not isinstance(raw_price, bool)
        if not isinstance(symbol, str) or not price_ok:
            raise UnexpectedResponseError("entrada do lote sem symbol/price válidos")
        try:
            price = float(raw_price)  # Binance devolve o preço como string
        except ValueError as exc:
            raise UnexpectedResponseError(f"preço ilegível da Binance: {raw_price!r}") from exc
        results[symbol] = QuoteResult(
            symbol=symbol, price=price, price_at=price_at, change_pct=None
        )
    return results


def _parse_response(response: httpx.Response) -> tuple[int, object]:
    """Devolve ``(status, payload ou None)`` — corpo ilegível vira None (o status decide)."""
    try:
        payload: object = response.json()
    except ValueError:
        return response.status_code, None
    return response.status_code, payload


def _request(client: httpx.Client, params: dict[str, str]) -> tuple[int, object]:
    """Requisição ao endpoint ativo com failover de espelho (D-04).

    451 ou erro de transporte no principal troca para o espelho (uma vez,
    com evento único) e refaz a chamada lá; re-probe horário que falha só
    atualiza o carimbo e segue no espelho. Transporte do próprio espelho
    vira ``SourceUnavailableError`` sem encadear a exceção httpx — ela
    carrega a URL da requisição, que nunca pode escapar por um traceback
    (K2); a mensagem cita só o tipo da falha.
    """
    now = _now()
    base, reprobe = _active_base(now)
    try:
        response = client.get(base + _TICKER_PATH, params=params)
    except httpx.TransportError as exc:
        if base == _MIRROR_BASE:
            raise SourceUnavailableError(f"erro de rede na Binance: {type(exc).__name__}") from None
        if reprobe:  # re-probe do principal falhou: permanece no espelho por mais 1 h
            _mirror_state.last_primary_probe_at = now
        else:
            _switch_to_mirror(now)
        return _parse_response(_get_on_mirror(client, params))
    if response.status_code == 451 and base == _PRIMARY_BASE:
        if reprobe:
            _mirror_state.last_primary_probe_at = now
        else:
            _switch_to_mirror(now)
        return _parse_response(_get_on_mirror(client, params))
    if reprobe and base == _PRIMARY_BASE:
        _switch_to_primary()  # re-probe do re-probe? não: o principal respondeu
    return _parse_response(response)


def _get_on_mirror(client: httpx.Client, params: dict[str, str]) -> httpx.Response:
    """Refaz a chamada no espelho; erro de transporte vira falha tipada (K2)."""
    try:
        return client.get(_MIRROR_BASE + _TICKER_PATH, params=params)
    except httpx.TransportError as exc:
        raise SourceUnavailableError(f"erro de rede na Binance: {type(exc).__name__}") from None


def _raise_for_status(status: int) -> None:
    """Mapeia os status de nível de fonte, citando só kind e código (K2)."""
    if status in _RATE_LIMIT_STATUS:
        raise RateLimitError(f"Binance limitou as requisições (HTTP {status})")
    if status == 451:
        raise SourceUnavailableError("Binance bloqueou a requisição (HTTP 451)")
    if status >= 500:
        raise SourceUnavailableError(f"Binance indisponível (HTTP {status})")


def _fetch_one_by_one(
    client: httpx.Client, symbols: list[str]
) -> tuple[dict[str, QuoteResult], dict[str, SourceError]]:
    """Repetição 1-a-1 do lote recusado por -1121 — feita UMA única vez (D-02).

    Cada símbolo sai com ``symbol=`` individual (shape objeto no parser): o
    ruim responde 400 -1121 e vira ``SymbolNotFoundError``; os válidos voltam
    como ``QuoteResult``. 429/418 no meio do 1-a-1 aborta na hora (limite por
    IP — continuar seria pedir ban); demais falhas ficam por símbolo.
    """
    quotes: dict[str, QuoteResult] = {}
    errors: dict[str, SourceError] = {}
    for symbol in symbols:
        status, payload = _request(client, {"symbol": symbol})
        _raise_for_status(status)
        invalid = (
            status == 400
            and isinstance(payload, dict)
            and payload.get("code") == _INVALID_SYMBOL_CODE
        )
        if invalid:
            errors[symbol] = SymbolNotFoundError(
                f"símbolo inválido na Binance: {symbol}", symbol=symbol
            )
            continue
        if status != 200 or not isinstance(payload, (dict, list)):
            errors[symbol] = UnexpectedResponseError(
                f"resposta inesperada da Binance na chamada única (HTTP {status})",
                symbol=symbol,
            )
            continue
        try:
            parsed = _parse_tickers(payload)
        except UnexpectedResponseError as exc:
            errors[symbol] = exc
            continue
        if symbol not in parsed:
            errors[symbol] = UnexpectedResponseError(
                "resposta única da Binance sem o símbolo pedido", symbol=symbol
            )
            continue
        quotes[symbol] = parsed[symbol]
    return quotes, errors


def fetch_batch(
    client: httpx.Client, symbols: list[str]
) -> tuple[dict[str, QuoteResult], dict[str, SourceError]]:
    """Consulta os símbolos em UM lote; devolve ``(acertos, erros por símbolo)``.

    Falhas de nível de fonte (rate limit, indisponível, bloqueio 451 nos dois
    endereços, rede, shape raiz) levantam ``SourceError``; falha por símbolo
    (o inválido do -1121) vem no segundo elemento do retorno para a rodada
    gravar em ``quote.last_fetch_error``. O espelho ativo é transparente: é
    apenas o base URL escolhido por ``_request`` (D-04).
    """
    # Separadores compactos (G-03-3): a gramática do parâmetro symbols
    # recusa espaço após a vírgula — json.dumps padrão sai
    # '["A", "B"]' e a Binance responde 400 code -1100 ("Illegal
    # characters"), perdendo a cotação da rodada inteira (prova ao vivo no
    # 03-UAT.md). Compacto: '["A","B"]' → 200 com o lote inteiro.
    status, payload = _request(
        client, {"symbols": json.dumps(list(symbols), separators=(",", ":"))}
    )
    _raise_for_status(status)
    if status == 400:
        if isinstance(payload, dict) and payload.get("code") == _INVALID_SYMBOL_CODE:
            return _fetch_one_by_one(client, list(symbols))
        # Outro 400 é a chamada inteira errada — repetir cego é proibido (Pitfall 2).
        raise UnexpectedResponseError("Binance recusou o lote (HTTP 400)")
    if status != 200:
        raise UnexpectedResponseError(f"resposta inesperada da Binance (HTTP {status})")
    if payload is None:
        raise UnexpectedResponseError("resposta ilegível da Binance (HTTP 200)")
    return _parse_tickers(payload), {}
