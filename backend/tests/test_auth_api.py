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
from baixou.web.auth import _DUMMY_HASH, ensure_setup_token
from baixou.web.main import create_app
from baixou.web.passwords import token_digest, verify_password
from fastapi.testclient import TestClient

SENHA = "senha-super-segura-123"
SENHA_CURTA = "curta-senha"  # 11 caracteres — abaixo do mínimo de 12
SENHA_ERRADA = "senha-incorreta"
# A MESMA literal de auth.py (_DUMMY_HASH): a senha que "acerta" o hash
# fictício do caminho de usuário inexistente (borda do CR-01).
SENHA_DUMMY = "hash-ficticio-para-igualar-o-tempo-de-resposta"


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


def test_senha_literal_do_hash_ficticio_em_usuario_inexistente_responde_401_identico_e_registra_falha(  # noqa: E501 — nome exigido pelo plano 04-05 (borda do CR-01)
    settings, capsys
):
    """Borda do hash fictício (CR-01): a senha que "acerta" o _DUMMY_HASH num
    usuário inexistente tem de dar o MESMO 401 byte a byte do caminho de senha
    errada — sem abrir sessão e COM a falha contada no bloqueio (AUTH-02/SC-2).
    """
    # Ligação teste↔código: o literal do teste exerce a borda REAL do módulo
    # (se a senha do hash fictício mudar um dia, o teste avisa aqui).
    assert verify_password(SENHA_DUMMY, _DUMMY_HASH)
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
        # referência: caminho de senha errada (usuário EXISTENTE)
        errada = client.post(
            "/api/auth/login", json={"username": "admin", "password": SENHA_ERRADA}
        )
        # borda: usuário inexistente + a senha literal do hash fictício
        dummy = client.post(
            "/api/auth/login", json={"username": "fantasma", "password": SENHA_DUMMY}
        )
    assert dummy.status_code == errada.status_code == 401
    assert dummy.content == errada.content  # byte a byte — indistinguível (T-04-04)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        # nenhuma sessão aberta para usuário inexistente
        assert conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()["n"] == 0
        falhas = {
            row["key"]: row["failures"]
            for row in conn.execute("SELECT key, failures FROM login_attempt")
        }
    finally:
        conn.close()
    # a borda CONTA como falha (o contador do bloqueio volta a cobri-la):
    # user:fantasma estreia com 1; ip:testclient soma a referência + a borda.
    # (user:admin também está lá, com 1 — da tentativa de referência.)
    assert falhas["user:fantasma"] == 1
    assert falhas["ip:testclient"] == 2
    assert falhas["user:admin"] == 1


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
    # Forma completa desde o 04-02 (quote via LEFT JOIN + moeda de currency_for),
    # com paused exposto desde o 04-03 (WATCH-03) — o mínimo do tracer 04-01
    # foi substituído pelo contrato congelado do plano.
    assert resposta.json() == {
        "itens": [
            {
                "symbol": "PETR4",
                "market": "b3",
                "target_below": 38.5,
                "paused": False,
                "currency": "R$",
                "price": None,
                "price_at": None,
                "change_pct": None,
                "alerted": False,
                "alerted_at": None,
                "notify_state": None,
            },
            {
                "symbol": "BTCUSDT",
                "market": "crypto",
                "target_below": 350000.0,
                "paused": False,
                "currency": "US$",
                "price": None,
                "price_at": None,
                "change_pct": None,
                "alerted": False,
                "alerted_at": None,
                "notify_state": None,
            },
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


def test_guard_aceita_mutacao_sem_corpo_conforme_o_contrato_e_origin_divergente_sem_corpo_403(
    settings, capsys
):
    """WR-01: o contrato congelado declara logout e DELETE SEM requestBody — um
    cliente gerado do openapi.json não envia Content-Type nessas operações, e o
    guard não pode recusá-las com 415. A conferência de Origin permanece
    INCONDICIONAL: mutante sem corpo com Origin divergente continua 403.
    """
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
        logado = client.post("/api/auth/login", json={"username": "admin", "password": SENHA})
        assert logado.status_code == 200
        token_sessao = client.cookies.get("baixou_session")
        assert token_sessao
        # perna 1 — Origin divergente SEM corpo: a camada anti-CSRF segue inteira
        divergente = client.post(
            "/api/auth/logout", headers={"Origin": "https://evil.example"}
        )
        assert divergente.status_code == 403
        # perna 2 — logout SEM corpo e SEM Content-Type (a forma declarada pelo
        # contrato) com sessão válida → 204
        saida = client.post("/api/auth/logout")
        assert saida.status_code == 204
    # perna 3 — a sessão morreu no logout da perna 2: o token capturado já
    # não autentica
    with _client(settings) as fresco:
        morto = fresco.get("/api/watch", cookies={"baixou_session": token_sessao})
    assert morto.status_code == 401


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


# --- Fase 4 (plano 04-01, Task 3): limite de tentativas de login (AUTH-02, SC-2) ---


def test_duas_falhas_nao_bloqueiam_a_terceira_tentativa_valida(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
        for _ in range(2):
            assert (
                client.post(
                    "/api/auth/login", json={"username": "admin", "password": SENHA_ERRADA}
                ).status_code
                == 401
            )
        valido = client.post("/api/auth/login", json={"username": "admin", "password": SENHA})
    assert valido.status_code == 200  # abaixo do limiar não há bloqueio


def test_terceira_falha_bloqueia_e_responde_429_com_retry_after(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
        for _ in range(3):
            assert (
                client.post(
                    "/api/auth/login", json={"username": "admin", "password": SENHA_ERRADA}
                ).status_code
                == 401
            )
        # 4ª tentativa MESMO com a senha correta: o bloqueio responde antes
        bloqueado = client.post("/api/auth/login", json={"username": "admin", "password": SENHA})
    assert bloqueado.status_code == 429
    retry_after = int(bloqueado.headers["retry-after"])
    assert 1 <= retry_after <= 30  # segundos restantes, inteiro, dentro da 1ª espera
    assert bloqueado.json() == {"detail": "muitas tentativas — aguarde e tente novamente"}
    # o bloqueio foi gravado nos DOIS keys (usuário E IP) com espera de ~30 s
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        linhas = {
            row["key"]: row
            for row in conn.execute("SELECT key, failures, blocked_until FROM login_attempt")
        }
    finally:
        conn.close()
    agora = datetime.now(UTC)
    for key in ("user:admin", "ip:testclient"):
        assert key in linhas
        assert linhas[key]["failures"] == 3
        restante = datetime.fromisoformat(linhas[key]["blocked_until"]) - agora
        assert timedelta(seconds=25) < restante <= timedelta(seconds=30)


def test_escada_da_espera_e_pura_dobra_e_teta_em_15_minutos():
    from baixou.web.auth import _wait_seconds  # sonda direta da função pura de módulo

    assert _wait_seconds(0) == 0
    assert _wait_seconds(2) == 0
    assert _wait_seconds(3) == 30
    assert _wait_seconds(4) == 60
    assert _wait_seconds(5) == 120
    assert _wait_seconds(7) == 480
    assert _wait_seconds(8) == 900  # 960 estoura o teto: min(30*2**5, 900)
    assert _wait_seconds(9) == 900
    assert _wait_seconds(15) == 900  # teto de 15 min


def test_tentativa_bloqueada_nao_incrementa_o_contador(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
        for _ in range(3):
            client.post("/api/auth/login", json={"username": "admin", "password": SENHA_ERRADA})
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin", "password": SENHA}
            ).status_code
            == 429
        )
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        falhas = {
            row["key"]: row["failures"]
            for row in conn.execute("SELECT key, failures FROM login_attempt")
        }
    finally:
        conn.close()
    assert falhas == {"user:admin": 3, "ip:testclient": 3}  # o 429 não conta


def test_login_bem_sucedido_apaga_os_dois_keys_e_recomeca_a_escada(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
        for _ in range(2):
            client.post("/api/auth/login", json={"username": "admin", "password": SENHA_ERRADA})
        revalidado = client.post("/api/auth/login", json={"username": "admin", "password": SENHA})
        assert revalidado.status_code == 200
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        chaves = [row["key"] for row in conn.execute("SELECT key FROM login_attempt")]
    finally:
        conn.close()
    assert chaves == []  # sucesso apaga user:<x> e ip:<ip> de uma vez
    with _client(settings) as client:
        client.post("/api/auth/login", json={"username": "admin", "password": SENHA_ERRADA})
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        falhas = {
            row["key"]: row["failures"]
            for row in conn.execute("SELECT key, failures FROM login_attempt")
        }
    finally:
        conn.close()
    assert falhas == {"user:admin": 1, "ip:testclient": 1}  # escada recomeça em 1


def test_bloqueio_de_outro_usuario_ou_outro_ip_nao_afeta_o_login(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token).status_code == 201
    futuro = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        with write_tx(conn):
            conn.executemany(
                "INSERT INTO login_attempt (key, failures, blocked_until) VALUES (?, ?, ?)",
                [("user:outro", 9, futuro), ("ip:203.0.113.99", 9, futuro)],
            )
    finally:
        conn.close()
    with _client(settings) as client:
        resposta = client.post("/api/auth/login", json={"username": "admin", "password": SENHA})
    assert resposta.status_code == 200  # nenhum dos SEUS dois keys está bloqueado


def test_log_de_falha_nunca_contem_username_nem_senha_tentada(settings, capsys):
    token = _migra_e_pega_token(settings, capsys)
    with _client(settings) as client:
        assert _cria_admin(client, token, username="admin-silencioso").status_code == 201
        assert (
            client.post(
                "/api/auth/login", json={"username": "admin-silencioso", "password": SENHA_ERRADA}
            ).status_code
            == 401
        )
    saida = capsys.readouterr().out
    assert "login_failed" in saida
    # K2/T-04-01: entrada de atacante não vira log — nem username nem senha
    assert "admin-silencioso" not in saida
    assert SENHA_ERRADA not in saida
