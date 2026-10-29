"""Tracer da Fase 4: token do log → setup → login → rota protegida → logout.

Cobre o caminho único de ponta a ponta sob pytest com banco real em tmp_path
e ZERO rede (TestClient, molde test_healthz.py): SC-1 (token impresso UMA vez
e só sem usuário, senha < 12 recusada), sessão no servidor com cookie
HttpOnly/SameSite=Strict, 401 byte a byte idêntico para usuário inexistente e
senha errada (T-04-04), guard de mutação (415/403) e GET /api/watch mínimo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from baixou.core.config import load_settings
from baixou.core.db import WORKER_TIMEOUT_S, connect, write_tx
from baixou.core.migrations import apply_migrations
from baixou.web.auth import ensure_setup_token
from baixou.web.main import create_app
from baixou.web.passwords import token_digest
from fastapi.testclient import TestClient

SENHA = "senha-super-segura-123"
SENHA_CURTA = "curta-senha"  # 11 caracteres — abaixo do mínimo de 12
SENHA_ERRADA = "senha-incorreta"


def _client(settings) -> TestClient:
    return TestClient(create_app(settings))


def _migra_e_pega_token(settings, capsys) -> str:
    apply_migrations(settings.db_path)
    ensure_setup_token(settings)
    return _token_do_log(capsys.readouterr().out)


def _token_do_log(saida: str) -> str:
    for line in saida.splitlines():
        if "setup_token_created" in line:
            resto = line.split("token de primeiro acesso: ", 1)[1]
            return resto.split(" ", 1)[0].strip()
    raise AssertionError("evento setup_token_created ausente do log")


def _cria_admin(client: TestClient, token: str, username: str = "admin", password: str = SENHA):
    return client.post(
        "/api/setup", json={"token": token, "username": username, "password": password}
    )


def test_setup_pending_true_sem_usuario(settings, capsys):
    _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        response = client.get("/api/setup")
    assert response.status_code == 200
    assert response.json() == {"setup_pending": True}


def test_fluxo_e2e_do_tracer_token_do_log_ate_logout(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        # login recusado: o usuário ainda não existe
        recusado = client.post("/api/auth/login", json={"username": "admin", "password": SENHA})
        assert recusado.status_code == 401
        # setup com token errado → 401
        assert _cria_admin(client, "token-errado").status_code == 401
        # setup com o token capturado do log → 201
        assert _cria_admin(client, token).status_code == 201
        # setup de novo → 403 (o administrador único já existe)
        assert _cria_admin(client, token, username="outro").status_code == 403
        # login válido → 200 com cookie HttpOnly e SameSite=strict
        logado = client.post(
            "/api/auth/login", json={"username": "admin", "password": SENHA, "remember": True}
        )
        assert logado.status_code == 200
        cookie = logado.headers["set-cookie"]
        assert "httponly" in cookie.lower()
        assert "samesite=strict" in cookie.lower()
        token_sessao = client.cookies.get("baixou_session")
        assert token_sessao
        # rota protegida SEM cookie → 401
        with _client(settings) as anonimo:
            negado = anonimo.get("/api/watch")
        assert negado.status_code == 401
        assert negado.json() == {"detail": "sessão ausente ou expirada"}
        # com o cookie → 200 e lista vazia
        lista = client.get("/api/watch")
        assert lista.status_code == 200
        assert lista.json() == {"itens": []}
        # logout (método mutante: exige Content-Type json) → 204 e a MESMA
        # sessão capturada já não acessa
        assert client.post("/api/auth/logout", json={}).status_code == 204
        with _client(settings) as fresco:
            morto = fresco.get("/api/watch", cookies={"baixou_session": token_sessao})
        assert morto.status_code == 401


def test_senha_de_11_caracteres_recusada_e_12_cria_o_admin(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        recusa = _cria_admin(client, token, password=SENHA_CURTA)
        assert recusa.status_code == 422
        item = recusa.json()["erros"][0]
        assert item["campo"] == "password"
        assert item["problema"]
        aceito = _cria_admin(client, token, password="doze-caracteres")  # exatamente 12
        assert aceito.status_code == 201
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM setup").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 1
    finally:
        conn.close()


def test_401_identico_para_usuario_inexistente_e_senha_errada(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
        inexistente = client.post(
            "/api/auth/login", json={"username": "fantasma", "password": SENHA_ERRADA}
        )
        errada = client.post(
            "/api/auth/login", json={"username": "admin", "password": SENHA_ERRADA}
        )
    assert inexistente.status_code == errada.status_code == 401
    assert inexistente.content == errada.content  # byte a byte (T-04-04)


def test_username_so_espacos_recusado(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        resposta = _cria_admin(client, token, username="   ")
    assert resposta.status_code == 422
    assert resposta.json()["erros"][0]["campo"] == "username"


def test_token_do_log_aparece_uma_vez_e_so_sem_usuario(settings, capsys):
    apply_migrations(settings.db_path)
    ensure_setup_token(settings)
    saida = capsys.readouterr().out
    assert saida.count("setup_token_created") == 1
    token = _token_do_log(saida)
    assert token in saida  # o token CRU vai ao log UMA vez (exceção única do K2)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
    ensure_setup_token(settings)  # com usuário: nada é gerado nem logado
    assert capsys.readouterr().out.count("setup_token_created") == 0


def test_setup_pending_false_apos_criar_o_admin(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
        resposta = client.get("/api/setup")
    assert resposta.status_code == 200
    assert resposta.json() == {"setup_pending": False}


def test_watch_protegida_lista_itens_ordenados(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        with write_tx(conn):
            conn.executemany(
                "INSERT INTO watch (symbol, market, target_below) VALUES (?, ?, ?)",
                [("BTCUSDT", "crypto", 350000.0), ("PETR4", "b3", 38.5)],
            )
    finally:
        conn.close()
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": SENHA}
            ).status_code
            == 200
        )
        resposta = client.get("/api/watch")
    assert resposta.status_code == 200
    assert resposta.json() == {
        "itens": [
            {"symbol": "PETR4", "market": "b3", "target_below": 38.5},
            {"symbol": "BTCUSDT", "market": "crypto", "target_below": 350000.0},
        ]
    }  # ORDER BY market, symbol: b3 antes de crypto


def test_mutacao_sem_content_type_json_recusada_415(settings, capsys):
    _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        resposta = client.post("/api/setup", content=b"{}", headers={"Content-Type": "text/plain"})
    assert resposta.status_code == 415


def test_origin_divergente_403_e_origem_propria_aceita(settings, capsys):
    _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        divergente = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": SENHA},
            headers={"Origin": "https://evil.example"},
        )
        propria = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": SENHA},
            headers={"Origin": "https://testserver"},  # host do TestClient
        )
    assert divergente.status_code == 403
    assert propria.status_code == 401  # passou o guard: só as credenciais falham


# --- Fase 4 (plano 04-01, Task 2): "lembrar de mim" (D-10), validade sliding
# --- (D-11) e Secure do cookie pelo env COOKIE_SECURE (molde WR-01)


def _linha_sessao(settings, token_sessao: str):
    """Linha de sessions pelo digest do token (o banco não guarda o token cru)."""
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        return conn.execute(
            "SELECT * FROM sessions WHERE token_hash = ?", (token_digest(token_sessao),)
        ).fetchone()
    finally:
        conn.close()


def _admin_logado(settings, capsys, remember: bool = False):
    """Admin criado e logado; devolve o client (cookie na jarra) e a resposta."""
    token_setup = _migra_e_pega_token(settings, capsys)
    client = _client(settings)
    assert _cria_admin(client, token_setup).status_code == 201
    resposta = client.post(
        "/api/auth/login", json={"username": "admin", "password": SENHA, "remember": remember}
    )
    assert resposta.status_code == 200
    return client, resposta


def test_lembrar_de_mim_cookie_de_30_dias_e_remember_na_linha(settings, capsys):
    client, resposta = _admin_logado(settings, capsys, remember=True)
    assert "max-age=2592000" in resposta.headers["set-cookie"].lower()
    linha = _linha_sessao(settings, client.cookies.get("baixou_session"))
    assert linha["remember"] == 1
    restante = datetime.fromisoformat(linha["expires_at"]) - datetime.now(UTC)
    assert timedelta(days=29, hours=23) < restante <= timedelta(days=30)
    client.close()


def test_sem_lembrar_cookie_de_sessao_e_validade_de_12_horas(settings, capsys):
    client, resposta = _admin_logado(settings, capsys, remember=False)
    assert "max-age=" not in resposta.headers["set-cookie"].lower()
    linha = _linha_sessao(settings, client.cookies.get("baixou_session"))
    assert linha["remember"] == 0
    restante = datetime.fromisoformat(linha["expires_at"]) - datetime.now(UTC)
    assert timedelta(hours=11) < restante <= timedelta(hours=12)
    client.close()


def test_validade_sliding_reescreve_o_prazo_a_cada_uso(settings, capsys):
    client, _resposta = _admin_logado(settings, capsys, remember=True)
    antes = _linha_sessao(settings, client.cookies.get("baixou_session"))["expires_at"]
    assert client.get("/api/watch").status_code == 200  # uso autenticado renova
    linha = _linha_sessao(settings, client.cookies.get("baixou_session"))
    assert linha["expires_at"] > antes  # estritamente maior: TTL cheio de novo (D-11)
    assert linha["last_used_at"] is not None
    client.close()


def test_sessao_expirada_responde_401_e_apaga_a_linha(settings, capsys):
    client, _resposta = _admin_logado(settings, capsys, remember=False)
    token_sessao = client.cookies.get("baixou_session")
    passado = (datetime.now(UTC) - timedelta(hours=13)).isoformat()
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        with write_tx(conn):
            conn.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                (passado, token_digest(token_sessao)),
            )
    finally:
        conn.close()
    with _client(settings) as fresco:
        negado = fresco.get("/api/watch", cookies={"baixou_session": token_sessao})
    assert negado.status_code == 401
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0
    finally:
        conn.close()
    client.close()


def test_cookie_secure_sim_inclui_secure_mesmo_em_http(settings, monkeypatch, capsys):
    monkeypatch.setenv("COOKIE_SECURE", "sim")
    client, resposta = _admin_logado(load_settings(), capsys, remember=False)
    assert "; secure" in resposta.headers["set-cookie"].lower()
    client.close()


def test_cookie_secure_auto_em_http_nao_inclui_secure(settings, capsys):
    client, resposta = _admin_logado(settings, capsys, remember=False)  # padrão: auto
    assert "; secure" not in resposta.headers["set-cookie"].lower()
    client.close()
