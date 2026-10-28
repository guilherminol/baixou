"""Contrato comum das fontes de cotação: falhas tipadas, resultado e pregão (POLL-01, POLL-02).

Hierarquia ``SourceError`` com atributo ``kind`` estável em EN (rate_limit,
not_found, auth, unavailable, unexpected) — o worker decide pela mensagem e
pela repetição a partir do TIPO, nunca pelo texto do erro (arquitetura
§Interfaces de fontes). ``QuoteResult`` é o resultado normalizado por símbolo:
preço, hora da cotação em UTC ISO-8601 (mesmo formato do banco) e variação do
dia quando a fonte a fornece. ``b3_market_open`` é o filtro de pregão seg–sex
10h–18h no fuso configurável, via ZoneInfo com nome de zona IANA — nunca
offset fixo nem datetime naive (Pitfall 5, SC-1). Os horários são constantes
de módulo porque a env B3_OPEN/B3_CLOSE da proposta de arquitetura não foi
decidida no CONTEXT desta fase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

B3_FIRST_WEEKDAY = 0  # segunda
B3_LAST_WEEKDAY = 4  # sexta
B3_OPEN_HOUR = 10  # inclusivo
B3_CLOSE_HOUR = 18  # exclusivo


class SourceError(Exception):
    """Falha de fonte com ``kind`` estável em EN; opcionalmente por símbolo."""

    kind = "unexpected"

    def __init__(self, message: str, *, symbol: str | None = None) -> None:
        super().__init__(message)
        self.symbol = symbol


class RateLimitError(SourceError):
    """Limite de uso da fonte (ex.: 429 da brapi) — sem retry em laço."""

    kind = "rate_limit"


class SymbolNotFoundError(SourceError):
    """Símbolo não encontrado na fonte (ex.: resultados vazios da brapi)."""

    kind = "not_found"


class AuthError(SourceError):
    """Autenticação recusada pela fonte (ex.: token inválido — 401/403)."""

    kind = "auth"


class SourceUnavailableError(SourceError):
    """Fonte indisponível (5xx, erro de rede, bloqueio geográfico 451)."""

    kind = "unavailable"


class UnexpectedResponseError(SourceError):
    """Resposta ilegível ou com shape inesperado — formato à deriva (T-02-02)."""

    kind = "unexpected"


@dataclass(frozen=True)
class QuoteResult:
    """Resultado normalizado de uma cotação, por símbolo."""

    symbol: str
    price: float
    price_at: str  # UTC ISO-8601, mesmo formato %Y-%m-%dT%H:%M:%SZ do banco
    change_pct: float | None  # variação do dia quando a fonte a fornece


def utc_iso_from_epoch(epoch_seconds: float) -> str:
    """Converte epoch (segundos) para o formato de timestamp do banco (UTC)."""
    return datetime.fromtimestamp(epoch_seconds, tz=UTC).strftime(_TS_FORMAT)


def b3_market_open(now_utc: datetime, tz_name: str) -> bool:
    """Pregão da B3 aberto: seg–sex, 10h inclusivo a 18h exclusivo no fuso.

    O instante chega aware (UTC) e é convertido para o fuso ``tz_name`` (nome
    de zona IANA via ZoneInfo — a tabela IANA traz também o offset histórico,
    incluída a DST abolida em 2019; Pitfall 5).
    """
    local = now_utc.astimezone(ZoneInfo(tz_name))
    if not B3_FIRST_WEEKDAY <= local.weekday() <= B3_LAST_WEEKDAY:
        return False
    return B3_OPEN_HOUR <= local.hour < B3_CLOSE_HOUR
