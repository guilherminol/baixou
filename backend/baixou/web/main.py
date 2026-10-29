"""Serviço web (FastAPI): entrada, migrações, /healthz e API autenticada.

O /healthz é o contrato final desde o dia um: sem login, sem dados no corpo,
sem cabeçalhos de versão — mede a vida do worker (run.last_run) contra
2 × POLL_MINUTES. Fase 4: routers de auth e da lista sob /api, guard de
mutação (Content-Type application/json + Origin) e o token de primeiro acesso
impresso UMA vez no log quando não há usuário (SC-1).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from baixou.core.config import Settings, load_settings
from baixou.core.db import WEB_TIMEOUT_S, check_sqlite_version, connect
from baixou.core.log import configure
from baixou.core.migrations import apply_migrations
from baixou.web import auth, watch

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def create_app(settings: Settings) -> FastAPI:
    """Aplicação FastAPI com a documentação automática desligada (contrato da API)."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def guard_de_mutacao(request: Request, call_next):
        """Todo método mutante sob /api exige JSON e Origin do próprio host.

        Arquitetura (linha do contrato da API): Content-Type application/json
        obrigatório (415 sem ele) e conferência do Origin quando presente
        (host divergente → 403) — SameSite=Strict é a primeira camada, este
        guard é a segunda (T-04-03). O /healthz nunca passa por aqui: só
        caminhos iniciados por "/api" são inspecionados.
        """
        if request.method in _MUTATING_METHODS and request.url.path.startswith("/api"):
            content_type = request.headers.get("content-type", "")
            if not content_type.strip().lower().startswith("application/json"):
                return JSONResponse(
                    status_code=415,
                    content={"detail": "content-type application/json é obrigatório"},
                )
            origin = request.headers.get("origin")
            if origin is not None:
                host = request.headers.get("host", "")
                if urlparse(origin).netloc.lower() != host.strip().lower():
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "origin não corresponde ao host do serviço"},
                    )
        return await call_next(request)

    app.include_router(auth.router)
    app.include_router(watch.router)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> Response:
        deadline = timedelta(minutes=2 * settings.poll_minutes)
        try:
            conn = connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)
            try:
                row = conn.execute("SELECT last_run FROM run WHERE id = 1").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return Response(status_code=503)

        last_run = row["last_run"] if row is not None else None
        if not last_run:
            return Response(status_code=503)
        try:
            recorded = datetime.fromisoformat(str(last_run).replace("Z", "+00:00"))
        except TypeError, ValueError:
            # Qualquer falha de parse → 503 com corpo vazio; exceção NUNCA
            # chega ao cliente. O corpo é vazio também no 503: sem causa, sem
            # versão (proibição K1 / T-01-08 — o /healthz é só sinal de vida).
            return Response(status_code=503)
        if recorded.tzinfo is None:
            return Response(status_code=503)

        age = datetime.now(UTC) - recorded
        if timedelta(0) <= age < deadline:
            return Response(status_code=200)
        return Response(status_code=503)

    return app


def main() -> None:
    """Ordem do entrypoint: config → versão do SQLite → migrações → setup → servir."""
    settings = load_settings()
    configure(settings.log_level)
    check_sqlite_version()
    apply_migrations(settings.db_path)
    auth.ensure_setup_token(settings)  # imprime o token UMA vez, só sem usuário
    app = create_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=8000, proxy_headers=True)


if __name__ == "__main__":
    main()
