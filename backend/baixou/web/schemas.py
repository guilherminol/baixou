"""Schemas pydantic da API de autenticação e da lista + o envelope de erro D-08.

O envelope de erro é contrato CONGELADO desta fase (D-08, reversibility
costly): ``{"erros": [{"symbol", "campo", "problema"}]}`` em PT-BR, um item
por erro — a Fase 5 renderiza cada entrada direto no formulário.
``symbol`` é None fora de validação de ticker. ``WatchItem`` também é
contrato (WATCH-01/WATCH-02): serve de corpo do POST e do item do lote do
PUT (os campos de cotação têm default e são ignorados na entrada) e de item
da resposta do GET — a moeda sai da função única ``currency_for``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SetupRequest(BaseModel):
    """Corpo do POST /api/setup (SC-1): token do log + credenciais do admin."""

    token: str
    username: str
    password: str


class LoginRequest(BaseModel):
    """Corpo do POST /api/auth/login; "lembrar de mim" default desligado (D-10)."""

    username: str
    password: str
    remember: bool = False


class WatchItem(BaseModel):
    """Item da lista com a cotação e a moeda — corpo do POST e item do PUT (D-06).

    Na ENTRADA só symbol/market/target_below interessam (o resto tem default
    e campos extras são ignorados); na SAÍDA (GET/POST/PATCH/PUT) todos os
    campos vêm preenchidos — ``currency`` via ``currency_for``, campos de
    quote NULL quando não há cotação e ``notify_state`` como objeto JSON
    parseado ou null (nunca string crua). ``target_below`` exige > 0 (SC-4).
    """

    symbol: str
    market: Literal["b3", "crypto"]
    target_below: float = Field(gt=0)
    currency: str = ""
    price: float | None = None
    price_at: str | None = None
    change_pct: float | None = None
    alerted: bool = False
    alerted_at: str | None = None
    notify_state: dict[str, Any] | None = None


class WatchListResponse(BaseModel):
    """Envelope do GET/PUT /api/watch: a lista completa, ordenada (market, symbol)."""

    itens: list[WatchItem]


class PatchWatch(BaseModel):
    """Corpo do PATCH /api/watch/{symbol}/{market}: só o alvo muda (SC-4)."""

    target_below: float = Field(gt=0)


class PutWatch(BaseModel):
    """Corpo do PUT /api/watch em lote (D-06): cria/edita numa transação única.

    ``min_length=1``: PUT com corpo vazio é erro de FORMA (422). O lote NUNCA
    remove (D-07): itens ausentes do corpo permanecem — remoção existe só
    pelo DELETE explícito.
    """

    itens: list[WatchItem] = Field(min_length=1)


def erro_item(symbol: str | None, campo: str, problema: str) -> dict:
    """Monta UMA entrada do envelope D-08 (PT-BR em ``problema``)."""
    return {"symbol": symbol, "campo": campo, "problema": problema}
