"""Autenticação: setup com o token do log, login com sessão no servidor, logout.

Contratos (arquitetura §Segurança e login, AUTH-01/AUTH-02):

- Primeiro acesso (SC-1): sem usuário, o entrypoint gera um token de setup e o
  imprime UMA vez no log; POST /api/setup cria o admin com ele e apaga o
  registro — com usuário existente o setup é recusado e nenhum token novo
  aparece no log (o token no log é a ÚNICA exceção do K2, por contrato).
- Sessão NO servidor: banco guarda só o hash SHA-256 do token; o cookie
  ``baixou_session`` é HttpOnly, SameSite=Strict, Path=/ e Secure conforme
  ``settings.cookie_secure`` ("sim" sempre; "auto" só em https; "nao" nunca).
- Sem "lembrar de mim": cookie de sessão (sem Max-Age) e validade de 12 h;
  com ele: cookie de 30 dias (Max-Age) e validade de 30 dias — fixos no
  código (D-10). Validade sliding (D-11): cada uso renova o prazo.
- 401 com corpo idêntico para usuário inexistente e senha errada; o caminho
  de usuário inexistente roda scrypt contra um hash fictício para igualar o
  tempo de resposta (T-04-04).
- Segurança barata primeiro: a checagem de bloqueio (Task 3) não roda scrypt.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from baixou.core.config import Settings
from baixou.core.db import WEB_TIMEOUT_S, connect, write_tx
from baixou.core.log import log
from baixou.web.passwords import hash_password, new_token, token_digest, verify_password
from baixou.web.schemas import LoginRequest, SetupRequest, erro_item

router = APIRouter()

_COOKIE_NAME = "baixou_session"
_SESSION_TTL_SHORT = timedelta(hours=12)  # sem "lembrar" (D-10)
_SESSION_TTL_REMEMBER = timedelta(days=30)  # com "lembrar" (D-10)
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_MIN_PASSWORD_CHARS = 12

# Hash fictício: iguala o TEMPO do caminho de usuário inexistente ao de senha
# errada (T-04-04) — computado uma única vez na importação do módulo.
_DUMMY_HASH = hash_password("hash-ficticio-para-igualar-o-tempo-de-resposta")


def _now() -> datetime:
    return datetime.now(UTC)


def _ts(moment: datetime) -> str:
    return moment.strftime(_TS_FORMAT)


def _parse_ts(value: object) -> datetime | None:
    """Lê o timestamp ISO do banco; None em qualquer valor ilegível (= expirado)."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except TypeError, ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _open_db(request: Request) -> sqlite3.Connection:
    """Conexão por request (molde do /healthz), settings vindos do app.state."""
    settings: Settings = request.app.state.settings
    return connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)


def _secure_flag(settings: Settings, request: Request) -> bool:
    """Secure conforme COOKIE_SECURE: "sim" sempre; "auto" só em https; "nao" nunca.

    Em http://IP:porta um cookie Secure é descartado pelo navegador e o login
    falha sem explicação — daí os três valores (arquitetura §Senha e sessão).
    """
    if settings.cookie_secure == "sim":
        return True
    if settings.cookie_secure == "nao":
        return False
    return request.url.scheme == "https"


def require_session(request: Request) -> sqlite3.Row:
    """Dependência das rotas protegidas: devolve a linha da sessão válida.

    Cookie ausente, sessão inexistente ou expirada → 401; a linha expirada é
    apagada do banco (higiene: linha morta não acumula).
    """
    token = request.cookies.get(_COOKIE_NAME)
    conn = _open_db(request)
    try:
        row = None
        if token:
            row = conn.execute(
                "SELECT token_hash, user_id, expires_at, remember FROM sessions "
                "WHERE token_hash = ?",
                (token_digest(token),),
            ).fetchone()
        if row is not None:
            expires_at = _parse_ts(row["expires_at"])
            if expires_at is None or expires_at <= _now():
                with write_tx(conn):
                    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (row["token_hash"],))
                row = None
        if row is None:
            raise HTTPException(status_code=401, detail="sessão ausente ou expirada")
        return row
    finally:
        conn.close()


# Assinatura de rota protegida — molde Annotated (B008-clean) das rotas com sessão.
Sessao = Annotated[sqlite3.Row, Depends(require_session)]


def ensure_setup_token(settings: Settings) -> None:
    """Sem usuário: gera o token de primeiro acesso e o imprime UMA vez no log.

    O token CRU vai ao log NESTA linha e só nela — exceção única do K2, por
    contrato da arquitetura ("o web gera um token de configuração e o imprime
    no log do container"). Com usuário existente: nada é gerado nem logado.
    """
    conn = connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)
    try:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
            return
        token = new_token()
        with write_tx(conn):  # substitui qualquer registro antigo de setup
            conn.execute("DELETE FROM setup")
            conn.execute(
                "INSERT INTO setup (token_hash, created_at) VALUES (?, ?)",
                (token_digest(token), _ts(_now())),
            )
    finally:
        conn.close()
    log(
        "info",
        "web",
        "setup_token_created",
        text=(
            f"token de primeiro acesso: {token} — crie o administrador com ele; "
            "não será exibido novamente"
        ),
    )


@router.get("/api/setup")
def setup_status(request: Request) -> dict:
    """Diz se falta criar o administrador — sem sessão (contrato da API)."""
    conn = _open_db(request)
    try:
        pending = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None
    finally:
        conn.close()
    return {"setup_pending": pending}


@router.post("/api/setup")
def setup(request: Request, corpo: SetupRequest) -> Response:
    """Cria o administrador com o token do log — só funciona sem usuário (T-04-06)."""
    conn = _open_db(request)
    try:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
            return JSONResponse(
                status_code=403,
                content={"detail": "o administrador já existe — use o login"},
            )
        registered = conn.execute("SELECT token_hash FROM setup LIMIT 1").fetchone()
        if registered is None or not secrets.compare_digest(
            token_digest(corpo.token), registered["token_hash"]
        ):
            return JSONResponse(status_code=401, content={"detail": "token inválido"})
        if len(corpo.password) < _MIN_PASSWORD_CHARS:
            return JSONResponse(
                status_code=422,
                content={
                    "erros": [
                        erro_item(
                            None,
                            "password",
                            "a senha deve ter pelo menos 12 caracteres",
                        )
                    ]
                },
            )
        if not corpo.username.strip():
            return JSONResponse(
                status_code=422,
                content={"erros": [erro_item(None, "username", "informe um nome de usuário")]},
            )
        password_hash = hash_password(corpo.password)  # FORA da transação (16 MiB)
        with write_tx(conn):
            # Re-checagem dentro do lock: duas subidas simultâneas não criam
            # dois usuários (o produto é de usuário ÚNICO).
            if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                raise HTTPException(
                    status_code=403, detail="o administrador já existe — use o login"
                )
            conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (corpo.username, password_hash, _ts(_now())),
            )
            conn.execute("DELETE FROM setup")  # token serve UMA vez (SC-1)
    finally:
        conn.close()
    log("info", "web", "admin_created", text="administrador criado — faça login com a senha")
    return JSONResponse(status_code=201, content={"detail": "administrador criado"})


def _record_login_failure(conn: sqlite3.Connection, keys: tuple[str, str]) -> None:
    """Estrutura do upsert de falha por key (AUTH-02); a escada de bloqueio
    completa — blocked_until e o 429 — é ligada na Task 3 deste plano."""
    with write_tx(conn):
        for key in keys:
            conn.execute(
                """
                INSERT INTO login_attempt (key, failures, blocked_until)
                VALUES (?, 1, NULL)
                ON CONFLICT (key) DO UPDATE SET failures = failures + 1
                """,
                (key,),
            )


@router.post("/api/auth/login")
def login(request: Request, corpo: LoginRequest) -> Response:
    """Abre a sessão no servidor e devolve o cookie (limite de tentativas: Task 3)."""
    settings: Settings = request.app.state.settings
    ip = request.client.host if request.client is not None else ""
    conn = _open_db(request)
    try:
        user = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (corpo.username,)
        ).fetchone()
        stored = user["password_hash"] if user is not None else _DUMMY_HASH
        if not verify_password(corpo.password, stored):
            _record_login_failure(conn, (f"user:{corpo.username}", f"ip:{ip}"))
            # MESMA resposta para usuário inexistente e senha errada (T-04-04):
            # mesmo status, mesmo corpo, mesmo tempo (scrypt dummy acima).
            raise HTTPException(status_code=401, detail="usuário ou senha inválidos")

        token = new_token()
        now = _now()
        ttl = _SESSION_TTL_REMEMBER if corpo.remember else _SESSION_TTL_SHORT
        # Microssegundos preservados (precedente WR-02): a validade sliding da
        # Task 2 reescreve expires_at e a comparação precisa distinguir usos
        # no mesmo segundo.
        with write_tx(conn):
            conn.execute(
                """
                INSERT INTO sessions
                    (token_hash, user_id, created_at, expires_at, last_used_at, remember)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token_digest(token),
                    user["id"],
                    now.isoformat(),
                    (now + ttl).isoformat(),
                    now.isoformat(),
                    1 if corpo.remember else 0,
                ),
            )
    finally:
        conn.close()
    response = JSONResponse(status_code=200, content={"detail": "sessão aberta"})
    response.set_cookie(
        _COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        path="/",
        secure=_secure_flag(settings, request),
        max_age=int(_SESSION_TTL_REMEMBER.total_seconds()) if corpo.remember else None,
    )
    return response


@router.post("/api/auth/logout")
def logout(request: Request, usuario: Sessao) -> Response:
    """Encerra a sessão no servidor e apaga o cookie (AUTH-01)."""
    del usuario
    token = request.cookies.get(_COOKIE_NAME) or ""
    conn = _open_db(request)
    try:
        with write_tx(conn):
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),))
    finally:
        conn.close()
    response = Response(status_code=204)
    response.delete_cookie(_COOKIE_NAME, path="/")
    return response
