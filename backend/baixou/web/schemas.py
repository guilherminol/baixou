"""Schemas pydantic da API de autenticação e o envelope de erro D-08.

O envelope de erro é contrato CONGELADO desta fase (D-08, reversibility
costly): ``{"erros": [{"symbol", "campo", "problema"}]}`` em PT-BR, um item
por erro — a Fase 5 renderiza cada entrada direto no formulário.
``symbol`` é None fora de validação de ticker.
"""

from __future__ import annotations

from pydantic import BaseModel


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


def erro_item(symbol: str | None, campo: str, problema: str) -> dict:
    """Monta UMA entrada do envelope D-08 (PT-BR em ``problema``)."""
    return {"symbol": symbol, "campo": campo, "problema": problema}
