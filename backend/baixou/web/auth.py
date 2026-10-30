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
  tempo de resposta (T-04-04) e a falha é registrada MESMO quando a senha
  "acerta" o hash fictício — re-checagem pós-verify (CR-01).
- Segurança barata primeiro: a checagem de bloqueio (Task 3) não roda scrypt.
"""

from __future__ import annotations

import math
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
_LOCK_THRESHOLD = 3  # falhas seguidas que abrem o bloqueio (AUTH-02)
_WAIT_BASE_S = 30  # 1ª espera: 30 s, dobrando a cada nova falha
_WAIT_CEILING_S = 900  # teto da espera: 15 min

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
            else:
                # D-11: validade sliding — todo uso autenticado renova o prazo
                # para o TTL cheio da PRÓPRIA linha (remember=1 → 30 dias,
                # senão 12 h) e registra o último uso: sessão em uso contínuo
                # nunca expira. Transação única e curta (disciplina WAL).
                ttl = _SESSION_TTL_REMEMBER if row["remember"] else _SESSION_TTL_SHORT
                agora = _now()
                with write_tx(conn):
                    conn.execute(
                        "UPDATE sessions SET expires_at = ?, last_used_at = ? WHERE token_hash = ?",
                        ((agora + ttl).isoformat(), agora.isoformat(), row["token_hash"]),
                    )
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
    # D-13 (ao criar, já entra logado): a MESMA write_tx que cria o usuário
    # abre a PRIMEIRA sessão — mesmo mecanismo do login — e o 201 seta o
    # cookie; usuário e sessão nascem atômicos, sem janela de
    # usuário-sem-sessão. (Nota: comentário e não docstring — a docstring vira
    # description no openapi e o contrato congelado não muda.)
    settings: Settings = request.app.state.settings
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
        token = new_token()  # token da primeira sessão (D-13) — fora do lock
        with write_tx(conn):
            # Re-checagem dentro do lock: duas subidas simultâneas não criam
            # dois usuários (o produto é de usuário ÚNICO). O raise aborta a tx
            # INTEIRA: sem user, sem sessão, sem cookie (T-04-06).
            if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
                raise HTTPException(
                    status_code=403, detail="o administrador já existe — use o login"
                )
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (corpo.username, password_hash, _ts(_now())),
            )
            user_id = cursor.lastrowid
            conn.execute("DELETE FROM setup")  # token serve UMA vez (SC-1)
            agora = _now()
            # D-13: a PRIMEIRA sessão nasce na MESMA transação do usuário —
            # um lock só (disciplina WAL). Microssegundos preservados
            # (precedente WR-02); sem "lembrar": TTL de 12 h (D-10).
            conn.execute(
                """
                INSERT INTO sessions
                    (token_hash, user_id, created_at, expires_at, last_used_at, remember)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    token_digest(token),
                    user_id,
                    agora.isoformat(),
                    (agora + _SESSION_TTL_SHORT).isoformat(),
                    agora.isoformat(),
                    0,
                ),
            )
    finally:
        conn.close()
    log("info", "web", "admin_created", text="administrador criado — sessão aberta no servidor")
    response = JSONResponse(status_code=201, content={"detail": "administrador criado"})
    response.set_cookie(
        _COOKIE_NAME,
        token,
        httponly=True,
        samesite="strict",
        path="/",
        secure=_secure_flag(settings, request),
        max_age=None,
    )
    return response


def _wait_seconds(failures: int) -> int:
    """Escada da espera (AUTH-02): 0 abaixo de 3 falhas; 30 s dobrando até 15 min.

    Aritmética em inteiros — mesmo precedente do 80% da cota (calls*5 >=
    4*limit): 0.8*15000 em float dava 12000.000000000002. Falhas 3→30 s,
    4→60 s, 5→120 s … 9→900 s (teto).
    """
    if failures < _LOCK_THRESHOLD:
        return 0
    return min(_WAIT_BASE_S * 2 ** (failures - _LOCK_THRESHOLD), _WAIT_CEILING_S)


def _blocked_retry_after(conn: sqlite3.Connection, keys: tuple[str, str]) -> tuple[str, int] | None:
    """Consulta BARATA do bloqueio: sem scrypt, sem escrita (T-04-01).

    Devolve (key_kind, segundos restantes arredondados para cima) do primeiro
    key com blocked_until no futuro, ou None — o bloqueio não pode custar
    memória nem dar oracle de tempo ao atacante. blocked_until ilegível não
    bloqueia (a linha é morta como sessão expirada).
    """
    agora = _now()
    for key_kind, key in (("user", keys[0]), ("ip", keys[1])):
        row = conn.execute(
            "SELECT blocked_until FROM login_attempt WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            continue
        bloqueado_ate = _parse_ts(row["blocked_until"])
        if bloqueado_ate is None or bloqueado_ate <= agora:
            continue
        restante = (bloqueado_ate - agora).total_seconds()
        return key_kind, max(1, math.ceil(restante))
    return None


def _record_login_failure(conn: sqlite3.Connection, keys: tuple[str, str]) -> None:
    """Upsert de falha por key numa write_tx única (AUTH-02).

    failures só cresce em falha VERIFICADA; do 3º erro em diante o key nasce
    bloqueado por agora + espera da escada (NULL abaixo do limiar). O log
    carrega só o KIND do key — nunca o username digitado nem a senha tentada
    (entrada de atacante não vira log; K2).
    """
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
            failures = conn.execute(
                "SELECT failures FROM login_attempt WHERE key = ?", (key,)
            ).fetchone()["failures"]
            espera = _wait_seconds(failures)
            bloqueado_ate = None if espera == 0 else _ts(_now() + timedelta(seconds=espera))
            conn.execute(
                "UPDATE login_attempt SET blocked_until = ? WHERE key = ?",
                (bloqueado_ate, key),
            )
            log(
                "warning",
                "web",
                "login_failed",
                key_kind=key.split(":", 1)[0],
                reason="invalid_credentials",
                text="falha de login — credenciais inválidas",
            )


@router.post("/api/auth/login")
def login(request: Request, corpo: LoginRequest) -> Response:
    """Abre a sessão no servidor e devolve o cookie; resistente a força bruta.

    Ordem barata-primeiro (T-04-01): o bloqueio é consultado ANTES de qualquer
    scrypt — tentativa bloqueada não disputa slot do semáforo (16 MiB por
    hashing) e não incrementa contador.
    """
    settings: Settings = request.app.state.settings
    ip = request.client.host if request.client is not None else ""
    user_key = f"user:{corpo.username}"
    ip_key = f"ip:{ip}"
    conn = _open_db(request)
    try:
        bloqueio = _blocked_retry_after(conn, (user_key, ip_key))
        if bloqueio is not None:
            log(
                "warning",
                "web",
                "login_blocked",
                key_kind=bloqueio[0],
                text="muitas tentativas — login temporariamente bloqueado",
            )
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(bloqueio[1])},
                content={"detail": "muitas tentativas — aguarde e tente novamente"},
            )
        user = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (corpo.username,)
        ).fetchone()
        stored = user["password_hash"] if user is not None else _DUMMY_HASH
        senha_ok = verify_password(corpo.password, stored)
        if user is None or not senha_ok:
            # Re-checagem pós-verify (borda do dummy hash, CR-01): com usuário
            # inexistente a senha pode "acertar" o hash fictício — a tentativa
            # SEGURA sendo falha de login (o contador do bloqueio AUTH-02 volta
            # a cobrir a borda) e o ramo roda para OS DOIS motivos, nunca só
            # para `not senha_ok`.
            _record_login_failure(conn, (user_key, ip_key))
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
            # Sucesso zera a escada dos DOIS keys na MESMA transação (SC-2).
            conn.execute("DELETE FROM login_attempt WHERE key IN (?, ?)", (user_key, ip_key))
    finally:
        conn.close()
    log("info", "web", "login_ok", text="login autorizado — sessão aberta no servidor")
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
