"""Serviço web (FastAPI): entrada, migrações e GET /healthz (OPS-01).

O /healthz é o contrato final desde o dia um: sem login, sem dados no corpo,
sem cabeçalhos de versão — mede a vida do worker (run.last_run) contra
2 × POLL_MINUTES.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import uvicorn
from fastapi import FastAPI, Response

from baixou.core.config import Settings, load_settings
from baixou.core.db import WEB_TIMEOUT_S, check_sqlite_version, connect
from baixou.core.log import configure
from baixou.core.migrations import apply_migrations


def create_app(settings: Settings) -> FastAPI:
    """Aplicação FastAPI com a documentação automática desligada (contrato da API)."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

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
        except ValueError:
            return Response(status_code=503)
        if recorded.tzinfo is None:
            return Response(status_code=503)

        age = datetime.now(timezone.utc) - recorded
        if timedelta(0) <= age < deadline:
            return Response(status_code=200)
        return Response(status_code=503)

    return app


def main() -> None:
    """Ordem do entrypoint: config → versão do SQLite → migrações → servir."""
    settings = load_settings()
    configure(settings.log_level)
    check_sqlite_version()
    apply_migrations(settings.db_path)
    app = create_app(settings)
    uvicorn.run(app, host="0.0.0.0", port=8000, proxy_headers=True)


if __name__ == "__main__":
    main()
