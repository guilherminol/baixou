"""Serviço web (FastAPI): entrada, migrações, /healthz e API autenticada.

O /healthz é o contrato final desde o dia um: sem login, sem dados no corpo,
sem cabeçalhos de versão — mede a vida do worker (run.last_run) contra
2 × POLL_MINUTES. Fase 4: routers de auth e da lista sob /api, guard de
mutação (JSON quando há corpo + Origin sempre) e o token de primeiro acesso
impresso UMA vez no log quando não há usuário (SC-1).
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from baixou.core.config import Settings, load_settings
from baixou.core.db import WEB_TIMEOUT_S, check_sqlite_version, connect
from baixou.core.log import configure
from baixou.core.migrations import apply_migrations
from baixou.web import auth, notifier_test, status, watch
from baixou.web.schemas import erro_item

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _dist_frontend() -> Path:
    """Diretório do build Vite a servir (05-02).

    A env opcional ``FRONTEND_DIST`` vence; ausente ou VAZIA (molde WR-01:
    string vazia definida não conta como valor) resolve o padrão
    ``<repo>/frontend/dist`` — que aponta para o lugar certo no dev (raiz do
    repositório) e na imagem (``/app/frontend/dist``, copiado do estágio node
    do docker/Dockerfile).
    """
    env = os.environ.get("FRONTEND_DIST")
    if env is not None and env.strip():
        return Path(env)
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


def _problema_ptbr(erro: dict) -> str:
    """Traduz o erro do pydantic para a mensagem PT-BR curta do envelope D-08."""
    loc = erro.get("loc") or ("corpo",)
    campo = str(loc[-1])
    tipo = str(erro.get("type", ""))
    if tipo == "missing":
        return "campo obrigatório"
    if tipo == "greater_than":
        if campo == "target_below":
            return "o alvo deve ser maior que zero"
        return f"o valor deve ser maior que {(erro.get('ctx') or {}).get('gt', 0)}"
    if tipo == "greater_than_equal":
        return f"o valor deve ser maior ou igual a {(erro.get('ctx') or {}).get('ge', 0)}"
    if tipo == "literal_error":
        if campo == "market":
            return "market deve ser b3 ou crypto"
        return "valor fora das opções aceitas"
    if tipo == "too_short":
        if campo == "itens":
            return "informe ao menos um item"
        return "valor curto demais"
    if tipo in {"string_type", "string_unicode_type"}:
        return "o valor deve ser texto"
    if tipo in {"float_type", "float_parsing", "int_type", "int_parsing", "decimal_type"}:
        return "o valor deve ser numérico"
    if tipo in {"bool_type", "bool_parsing"}:
        return "o valor deve ser verdadeiro ou falso"
    if tipo == "string_too_short":
        return "texto curto demais"
    if tipo == "value_error":
        # Validador de modelo (ex.: PATCH sem nenhum campo, 04-03): a mensagem
        # PT-BR do validador vira o problema do envelope — o pydantic a embala
        # como "Value error, <mensagem>".
        msg = str(erro.get("msg", ""))
        return msg.removeprefix("Value error, ") or "valor inválido"
    return "valor inválido"


def _corpo_parseado(corpo: object) -> object:
    """O ``body`` da exceção pode vir cru (bytes): parseia JSON quando der."""
    if isinstance(corpo, bytes | bytearray | str):
        try:
            return json.loads(corpo)
        except ValueError:
            return None
    return corpo


def _simbolo_do_corpo(corpo: object, loc: tuple) -> str | None:
    """Best-effort: o symbol do item apontado pelo loc, normalizado (D-05).

    O PRIMEIRO segmento do loc do FastAPI é o literal ``"body"`` (a raiz do
    corpo da requisição), não uma chave do dict.
    """
    if loc and loc[0] == "body":
        loc = loc[1:]
    atual = _corpo_parseado(corpo)
    for parte in loc[:-1]:
        if isinstance(atual, dict) and isinstance(parte, str):
            atual = atual.get(parte)
        elif isinstance(atual, list) and isinstance(parte, int) and 0 <= parte < len(atual):
            atual = atual[parte]
        else:
            return None
    if isinstance(atual, dict):
        symbol = atual.get("symbol")
        if isinstance(symbol, str):
            return symbol.strip().upper()
    return None


def envelope_validacao(request: Request, exc: RequestValidationError) -> JSONResponse:
    """TODO 422 de FORMA da API fala o dialeto D-08 (um item por erro, PT-BR)."""
    del request
    erros = [
        erro_item(
            _simbolo_do_corpo(exc.body, tuple(erro.get("loc") or ())),
            str((erro.get("loc") or ("corpo",))[-1]),
            _problema_ptbr(erro),
        )
        for erro in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"erros": erros})


def create_app(settings: Settings) -> FastAPI:
    """Aplicação FastAPI com a documentação automática desligada (contrato da API)."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def guard_de_mutacao(request: Request, call_next):
        """Todo método mutante sob /api com CORPO exige JSON; Origin, sempre.

        Arquitetura (linha do contrato da API): Content-Type application/json
        obrigatório QUANDO a requisição carrega corpo (415 sem ele) e
        conferência do Origin INCONDICIONAL quando presente (host divergente →
        403) — SameSite=Strict é a primeira camada, este guard é a segunda
        (T-04-03). A isenção de Content-Type em requisição SEM corpo existe
        porque o contrato congelado (WR-01, opção (a)) declara DELETE
        /api/watch/{symbol}/{market} e POST /api/auth/logout sem requestBody:
        um cliente gerado do openapi.json não envia Content-Type nessas
        operações — e a camada anti-CSRF segue inteira (Origin conferida com
        ou sem corpo; logout sem sessão morre no require_session com 401;
        DELETE não é emitível por formulário HTML). O /healthz nunca passa
        por aqui: só caminhos iniciados por "/api" são inspecionados.
        """
        if request.method in _MUTATING_METHODS and request.url.path.startswith("/api"):
            content_type = request.headers.get("content-type", "")
            # Presença de corpo pelos cabeçalhos (nunca pela leitura do fluxo):
            # transfer-encoding OU content-length dígito > 0.
            content_length = request.headers.get("content-length", "").strip()
            tem_corpo = "transfer-encoding" in request.headers or (
                content_length.isdigit() and int(content_length) > 0
            )
            if tem_corpo and not content_type.strip().lower().startswith("application/json"):
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
    app.include_router(status.router)
    app.include_router(notifier_test.router)
    # Erros de forma do pydantic no MESMO envelope D-08 das recusas de negócio:
    # a Fase 5 renderiza os dois com o mesmo código (D-08, contrato congelado).
    app.add_exception_handler(RequestValidationError, envelope_validacao)

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

    # --- 05-02: cabeçalhos de segurança em TODAS as respostas ---
    # Registrado POR ÚLTIMO = camada MAIS EXTERNA da cebola de middlewares:
    # os cabeçalhos saem também nas recusas 415/403 do guard acima.
    # CSP 'self' SEM unsafe-inline é possível porque o build Vite só emite
    # arquivos externos (arquitetura §Requisições forjadas); frame-ancestors
    # 'none' fecha o clickjacking da UI de administração (T-05-07) e o
    # nosniff/remove-referrer completam a tríade do contrato.
    @app.middleware("http")
    async def cabecalhos_de_seguranca(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # --- 05-02: estáticos do build + fallback SPA ---
    # NO FIM de create_app porque no roteador do Starlette a ordem de
    # registro É a precedência: um catch-all '/{caminho:path}' registrado
    # antes sombrearia o /healthz e os routers de /api. Sem dist no disco,
    # nada é montado e o web segue só-API (tolerância para testes e dev sem
    # build — nunca depende de Node aqui).
    dist = _dist_frontend()
    if (dist / "index.html").is_file() and (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{caminho:path}", include_in_schema=False)
        def spa_fallback(caminho: str) -> Response:
            """Arquivo pedido dentro do dist; inexistente → index.html (SPA)."""
            pedido = (dist / caminho).resolve()
            # T-05-09 (path traversal): só sai arquivo que continua DENTRO do
            # dist; todo o resto cai no index.html, que o Router resolve.
            if pedido.is_file() and pedido.is_relative_to(dist.resolve()):
                return FileResponse(pedido)
            return FileResponse(dist / "index.html")

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
