"""POST /api/notifiers/{canal}/test (plano 05-01, D-05, NOTIF-02): aviso real sem gravação.

Sem rede real (molde test_watch_api.py: MockTransport na emenda
``notifier_test._cliente``) e com banco real em tmp_path: admin criado e
logado pelas PRÓPRIAS rotas de auth. O canal simulado aceita/recusa/falha de
transporte — o MÓDULO do canal converte em NotificationResult e o teste prova
o MAPEAMENTO (falha de entrega é RESULTADO, nunca erro HTTP — D-05). Zero
gravação é provada por snapshot de igualdade do banco antes/depois — EXCETO
``sessions``, que o guard de auth reescreve por contrato (validade sliding
D-11 renova expires_at/last_used_at em TODO uso autenticado; essa escrita é
do require_session, não do endpoint) — complementado por checagem estática do
código-fonte do módulo (sem ``write_tx``/``connect``/INSERT). A chamada
concorrente do NOTIF-02 usa ThreadPoolExecutor(2) sobre o mesmo app. K2:
nenhuma resposta contém token, chat_id, tópico ou "token=".
"""

from __future__ import annotations

import inspect
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
from baixou.core.db import WEB_TIMEOUT_S, connect
from baixou.core.migrations import apply_migrations
from baixou.web.auth import ensure_setup_token
from baixou.web.main import create_app
from fastapi.testclient import TestClient

SENHA = "senha-super-segura-123"
TOKEN_SECRETO = "segredo-token-nao-vazar"
TOPICO_SECRETO = "topico-secreto-nao-vazar"
CHAT_ID_SECRETO = "123456789"

# As 10 tabelas do esquema; sessions fica FORA da comparação por causa da
# renovação sliding do require_session (D-11) — escrita do guard de auth em
# todo uso autenticado, nunca do endpoint em teste (docstring do módulo).
_TABELAS = (
    "watch",
    "quote",
    "source_state",
    "quota",
    "run",
    "users",
    "login_attempt",
    "setup",
    "schema_migrations",
)


def _token_do_log(saida: str) -> str:
    for line in saida.splitlines():
        if "setup_token_created" in line:
            resto = line.split("token de primeiro acesso: ", 1)[1]
            return resto.split(" ", 1)[0].strip()
    raise AssertionError("evento setup_token_created ausente do log")


def _admin_logado(settings, capsys) -> TestClient:
    """Admin criado e logado pelas PRÓPRIAS rotas de auth (molde do tracer 04-01)."""
    apply_migrations(settings.db_path)
    ensure_setup_token(settings)
    token = _token_do_log(capsys.readouterr().out)
    client = TestClient(create_app(settings))
    assert (
        client.post(
            "/api/setup", json={"token": token, "username": "admin", "password": SENHA}
        ).status_code
        == 201
    )
    assert (
        client.post("/api/auth/login", json={"username": "admin", "password": SENHA}).status_code
        == 200
    )
    return client


def _canais_configurados(settings):
    """Settings com os DOIS canais configurados (valores-secretas para o K2)."""
    return replace(
        settings,
        telegram_token=TOKEN_SECRETO,
        telegram_chat_id=CHAT_ID_SECRETO,
        ntfy_topic=TOPICO_SECRETO,
    )


def _conn(settings) -> sqlite3.Connection:
    """Conexão de inspeção fora do client (WAL: leitura concorrente é segura)."""
    return connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)


def _snapshot(settings) -> dict[str, list[tuple]]:
    """Copia as linhas das tabelas de estado (sessions fora — ver docstring)."""
    conn = _conn(settings)
    try:
        return {
            tabela: [tuple(linha) for linha in conn.execute(f"SELECT * FROM {tabela}").fetchall()]
            for tabela in _TABELAS
        }
    finally:
        conn.close()


def _injeta_cliente(monkeypatch, handler) -> list[httpx.Request]:
    """Substitui ``notifier_test._cliente`` por MockTransport (emenda de teste)."""
    from baixou.web import notifier_test

    requisicoes: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        requisicoes.append(request)
        return handler(request)

    monkeypatch.setattr(
        notifier_test,
        "_cliente",
        lambda: httpx.Client(transport=httpx.MockTransport(recording)),
    )
    return requisicoes


def _canal_aceita(request: httpx.Request) -> httpx.Response:
    # sanity: o envio TELEGRAM usa o token real do Settings na URL do bot —
    # o ntfy via sem token na URL (o Bearer vai no cabeçalho, D-14).
    if request.url.host == "api.telegram.org":
        assert ("bot" + TOKEN_SECRETO) in str(request.url)
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})


def _canal_recusa_500(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"description": "Internal Server Error"})


def _canal_transporte_cai(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


# --- Cenário 1: canal configurado aceitando — 200 {ok: true} e ZERO gravação


def test_telegram_aceito_200_ok_true_e_nada_gravado(settings, capsys, monkeypatch):
    """D-05: aceite do canal é 200 com {ok: true, mensagem PT-BR}; banco intocado."""
    client = _admin_logado(_canais_configurados(settings), capsys)
    _injeta_cliente(monkeypatch, _canal_aceita)
    antes = _snapshot(settings)
    resposta = client.post("/api/notifiers/telegram/test")
    assert resposta.status_code == 200
    assert resposta.json() == {"ok": True, "mensagem": "aviso de teste aceito pelo canal"}
    assert _snapshot(settings) == antes  # NOTIF-02: nenhuma tabela de estado mudou


def test_ntfy_aceito_200_ok_true(settings, capsys, monkeypatch):
    """O MESMO contrato para o segundo canal da tupla CHANNEL_ORDER."""
    client = _admin_logado(_canais_configurados(settings), capsys)
    _injeta_cliente(monkeypatch, _canal_aceita)
    resposta = client.post("/api/notifiers/ntfy/test")
    assert resposta.status_code == 200
    assert resposta.json()["ok"] is True
    assert resposta.json()["mensagem"]  # PT-BR presente


# --- Cenário 2: canal recusando (HTTP 500) — 200 com ok false, nunca erro HTTP


def test_canal_recusa_500_vira_200_ok_false_citando_kind_e_http(settings, capsys, monkeypatch):
    """Recusa de entrega é RESULTADO do teste: 200 {ok: false} citando kind e HTTP 500."""
    client = _admin_logado(_canais_configurados(settings), capsys)
    _injeta_cliente(monkeypatch, _canal_recusa_500)
    antes = _snapshot(settings)
    resposta = client.post("/api/notifiers/telegram/test")
    assert resposta.status_code == 200  # NÃO é erro HTTP (D-05)
    corpo = resposta.json()
    assert corpo["ok"] is False
    assert "unavailable" in corpo["mensagem"]  # kind de 500
    assert "HTTP 500" in corpo["mensagem"]
    assert _snapshot(settings) == antes  # recusa também não grava


def test_canal_transporte_cai_200_ok_false_sem_codigo_http(settings, capsys, monkeypatch):
    """Falha de transporte: 200 {ok: false} citando o kind SEM código HTTP; sem segredo."""
    client = _admin_logado(_canais_configurados(settings), capsys)
    _injeta_cliente(monkeypatch, _canal_transporte_cai)
    resposta = client.post("/api/notifiers/telegram/test")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["ok"] is False
    assert "unavailable" in corpo["mensagem"]
    assert "HTTP" not in corpo["mensagem"]  # transporte não tem código
    assert TOKEN_SECRETO not in resposta.text
    assert CHAT_ID_SECRETO not in resposta.text
    assert "token=" not in resposta.text  # K2: nem o padrão de query/path


def test_ntfy_recusa_nao_vaza_topico(settings, capsys, monkeypatch):
    """K2 no ntfy: recusa 500 cita kind/HTTP, NUNCA o tópico (T-03-02)."""
    client = _admin_logado(_canais_configurados(settings), capsys)
    _injeta_cliente(monkeypatch, _canal_recusa_500)
    resposta = client.post("/api/notifiers/ntfy/test")
    assert resposta.status_code == 200
    assert resposta.json()["ok"] is False
    assert "HTTP 500" in resposta.json()["mensagem"]
    assert TOPICO_SECRETO not in resposta.text


# --- Cenário 4: canal não configurado — 409 sem chamar send


def test_canal_nao_configurado_409_sem_chamar_send(settings, capsys, monkeypatch):
    """Settings vazias → 409 {"detail": "canal não configurado"} e a rede NEM SAIU."""

    def violento(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"send não deveria ter sido chamado: {request.url.host}")

    client = _admin_logado(settings, capsys)  # padrões vazios: nenhum canal configurado
    _injeta_cliente(monkeypatch, violento)
    antes = _snapshot(settings)
    resposta = client.post("/api/notifiers/telegram/test")
    assert resposta.status_code == 409
    assert resposta.json() == {"detail": "canal não configurado"}
    assert _snapshot(settings) == antes


# --- Cenário 5: canal fora do Literal — 422 no envelope D-08 (nunca 500)


def test_canal_fora_do_literal_422_no_envelope_d08(settings, capsys):
    """'email' não é canal: 422 no envelope D-08 (handler global), nunca 500."""
    client = _admin_logado(_canais_configurados(settings), capsys)
    resposta = client.post("/api/notifiers/email/test")
    assert resposta.status_code == 422
    corpo = resposta.json()
    assert set(corpo) == {"erros"} and corpo["erros"]
    assert corpo["erros"][0]["campo"] == "canal"


# --- Cenário 6: guard de sessão + probe de concorrência (NOTIF-02)


def test_sem_sessao_responde_401(settings, capsys):
    """T-05-02: 401 ANTES de qualquer leitura/envio — o guard vale para o teste."""
    _admin_logado(_canais_configurados(settings), capsys)
    with TestClient(create_app(_canais_configurados(settings))) as anonimo:
        negado = anonimo.post("/api/notifiers/telegram/test")
    assert negado.status_code == 401


def test_duas_chamadas_concorrentes_respondem_e_nada_gravam(settings, capsys, monkeypatch):
    """Probe de concorrência (NOTIF-02): ambas recebem {ok, mensagem}; banco intocado."""
    client = _admin_logado(_canais_configurados(settings), capsys)
    _injeta_cliente(monkeypatch, _canal_aceita)
    antes = _snapshot(settings)
    with ThreadPoolExecutor(max_workers=2) as executor:
        respostas = list(
            executor.map(lambda _: client.post("/api/notifiers/telegram/test"), range(2))
        )
    assert all(r.status_code == 200 for r in respostas)
    assert all(r.json()["ok"] is True for r in respostas)
    assert _snapshot(settings) == antes


def test_modulo_do_endpoint_nao_tem_escrita_de_banco():
    """Backstop estático do NOTIF-02: o módulo não importa connect/write_tx nem grava SQL."""
    from baixou.web import notifier_test

    fonte = inspect.getsource(notifier_test)
    for proibido in ("write_tx", "INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert proibido not in fonte, f"endpoint de teste de canal não pode gravar: {proibido}"
