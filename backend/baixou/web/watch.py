"""Gestão da lista: GET /api/watch protegido por sessão (plano 04-01).

A FORMA do envelope e o guard de sessão nascem finais aqui; os campos de
cotação/moeda e os verbos de escrita (PUT/PATCH/DELETE do D-06/D-07) chegam
no plano 04-02.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from baixou.core.db import WEB_TIMEOUT_S, connect
from baixou.web.auth import Sessao

router = APIRouter()


@router.get("/api/watch")
def listar(request: Request, usuario: Sessao) -> dict:
    """Itens da lista do admin — exige sessão (del usuario: a linha só autentica)."""
    del usuario
    settings = request.app.state.settings
    conn = connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)
    try:
        rows = conn.execute(
            "SELECT symbol, market, target_below FROM watch ORDER BY market, symbol"
        ).fetchall()
    finally:
        conn.close()
    return {
        "itens": [
            {
                "symbol": row["symbol"],
                "market": row["market"],
                "target_below": row["target_below"],
            }
            for row in rows
        ]
    }
