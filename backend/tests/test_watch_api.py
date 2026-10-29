"""API da lista (plano 04-02): GET com cotação e moeda, CRUD item a item e PUT em lote.

Sem rede e com banco real em tmp_path (molde test_auth_api.py): usuário e
sessão criados PELAS ROTAS de auth, itens semeados direto no banco (molde
seed_watch de test_round.py). A forma do item é provada por IGUALDADE EXATA
de dicionário (precedente 03-05: igualdade chave-a-chave deixa sobra em
silêncio) — o contrato que a Fase 5 consome com tipos gerados sai daqui
congelado (WATCH-01/WATCH-02, D-06/D-07/D-08).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest
from baixou.core.db import WEB_TIMEOUT_S, connect
from baixou.core.migrations import apply_migrations
from baixou.web.auth import ensure_setup_token
from baixou.web.main import create_app
from fastapi.testclient import TestClient

SENHA = "senha-super-segura-123"


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


def _conn(settings) -> sqlite3.Connection:
    """Conexão de inspeção/semeadura fora do client (WAL: leitura concorrente é segura)."""
    return connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)


def _injeta_relogio_watch(monkeypatch, now_utc: datetime) -> None:
    """Congela o relógio da derivação de estado (D-02) em ``baixou.web.watch``.

    ``watch._agora`` é o ponto de injeção declarado no plano 05-01 — os
    handlers resolvem a função no escopo do módulo a cada request.
    """
    from baixou.web import watch

    monkeypatch.setattr(watch, "_agora", lambda: now_utc)


def _seed_watch(
    conn: sqlite3.Connection, symbol: str, market: str, target_below: float = 10.0
) -> None:
    """Semeia um ticker na watch (isolation_level=None: INSERT em autocommit)."""
    conn.execute(
        "INSERT INTO watch (symbol, market, target_below) VALUES (?, ?, ?)",
        (symbol, market, target_below),
    )


def _seed_quote(
    conn: sqlite3.Connection,
    symbol: str,
    market: str,
    *,
    price: float | None = None,
    price_at: str | None = None,
    change_pct: float | None = None,
    alerted: int = 0,
    alerted_at: str | None = None,
    alerted_target: float | None = None,
    notify_state: str | None = None,
) -> None:
    """Semeia a linha de cotação com estado de alerta à mão (formato D-15 quando JSON)."""
    conn.execute(
        """
        INSERT INTO quote (symbol, market, price, price_at, change_pct,
            alerted, alerted_at, alerted_target, notify_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            market,
            price,
            price_at,
            change_pct,
            alerted,
            alerted_at,
            alerted_target,
            notify_state,
        ),
    )


# --- Task 1: GET /api/watch completo — quote via LEFT JOIN e moeda (WATCH-02) ---


def test_get_devolve_itens_com_cotacao_e_moeda(settings, capsys, monkeypatch):
    """Item completo com moeda da fonte única de verdade e notify_state PARSEADO."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.2,
            price_at="2026-10-28T16:00:00Z",
            change_pct=-1.2,
            alerted=1,
            alerted_at="2026-10-28T16:05:00Z",
            alerted_target=38.5,
            notify_state='{"telegram": "ok"}',  # D-15 gravado à mão
        )
        _seed_watch(conn, "BTCUSDT", "crypto", 350000.0)  # sem cotação: campos NULL
    finally:
        conn.close()
    _injeta_relogio_watch(monkeypatch, _QUARTA_16H)  # 13:00 BRT: pregão aberto
    resposta = client.get("/api/watch")
    assert resposta.status_code == 200
    # Igualdade EXATA: a forma é contrato congelado para a Fase 5 (tipos gerados).
    assert resposta.json() == {
        "itens": [
            {
                "symbol": "PETR4",
                "market": "b3",
                "target_below": 38.5,
                "paused": False,  # exposto desde o 04-03 (WATCH-03)
                "currency": "R$",
                "price": 35.2,
                "price_at": "2026-10-28T16:00:00Z",
                "change_pct": -1.2,
                "alerted": True,
                "alerted_at": "2026-10-28T16:05:00Z",
                "notify_state": {"telegram": "ok"},  # objeto, NUNCA string crua
                "fresh": "ok",  # D-02 (05-01): estado derivado NO SERVIDOR
                "suspect": False,
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
                "fresh": "none",
                "suspect": False,
            },
        ]
    }


@pytest.mark.parametrize(
    ("symbol", "market", "moeda"),
    [
        ("PETR4", "b3", "R$"),
        ("BTCUSDT", "crypto", "US$"),
        ("ETHBRL", "crypto", "R$"),
        ("XYZUSDC", "crypto", "US$"),  # sufixo desconhecido → US$, padrão cripto
    ],
)
def test_moeda_do_item_deriva_do_sufixo(symbol, market, moeda, settings, capsys):
    """A moeda exposta é EXATAMENTE currency_for(symbol, market) — uma só verdade."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, symbol, market)
    finally:
        conn.close()
    resposta = client.get("/api/watch")
    assert resposta.status_code == 200
    itens = resposta.json()["itens"]
    assert len(itens) == 1
    assert itens[0]["currency"] == moeda


def test_lista_vazia_responde_200_com_itens_vazio(settings, capsys):
    """Borda de coleção: sem itens é 200 vazio, nunca 404."""
    client = _admin_logado(settings, capsys)
    resposta = client.get("/api/watch")
    assert resposta.status_code == 200
    assert resposta.json() == {"itens": []}


def test_ordenacao_deterministica_por_market_e_symbol(settings, capsys):
    """(market, symbol) ascendente e estável entre chamadas — sem depender do INSERT."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        for symbol, market in [
            ("ETHBRL", "crypto"),
            ("VALE3", "b3"),
            ("BTCUSDT", "crypto"),
            ("PETR4", "b3"),
        ]:
            _seed_watch(conn, symbol, market)
    finally:
        conn.close()
    primeira = [i["symbol"] for i in client.get("/api/watch").json()["itens"]]
    segunda = [i["symbol"] for i in client.get("/api/watch").json()["itens"]]
    assert primeira == ["PETR4", "VALE3", "BTCUSDT", "ETHBRL"]
    assert primeira == segunda  # estável


def test_get_sem_sessao_responde_401(settings, capsys):
    """Guard do 04-01 vale para a lista completa: sem cookie é 401."""
    _admin_logado(settings, capsys)
    with TestClient(create_app(settings)) as anonimo:
        negado = anonimo.get("/api/watch")
    assert negado.status_code == 401


# --- Task 2: POST/PATCH/DELETE item a item — validação híbrida D-03 e recusas
# --- D-02/D-04/D-05/D-08 (SC-4)

# Quarta 16:00 UTC = 13:00 em Brasília: dentro do pregão B3 (molde test_round).
_QUARTA_16H = datetime(2026, 10, 28, 16, 0, 0, tzinfo=UTC)
# Domingo 15:00 UTC = 12:00 em Brasília: fora do pregão (SC-1).
_DOMINGO = datetime(2026, 11, 1, 15, 0, 0, tzinfo=UTC)


def _violento(request: httpx.Request) -> httpx.Response:
    """Transporte que levanta se chamado — prova que a rede NEM SAIU (D-03)."""
    raise AssertionError(f"rede não deveria ter sido chamada: {request.url.host}")


def _injeta_transporte(monkeypatch, handler, requests: list[httpx.Request]) -> None:
    """Roteia o cliente de validação para MockTransport (zero rede real).

    Import tardio e emenda em ``watch._cliente_validacao``: na fase RED o
    módulo de validação ainda não existe e a coleção da suíte não pode quebrar.
    """
    from baixou.web import watch

    def recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    monkeypatch.setattr(
        watch,
        "_cliente_validacao",
        lambda: httpx.Client(transport=httpx.MockTransport(recording)),
        raising=False,
    )


def _congela_relogio(monkeypatch, now_utc: datetime) -> None:
    """Congela o relógio da validação D-03 (import tardio — módulo nasce no GREEN)."""
    from baixou.web import symbol_validation

    class _Congelado(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return now_utc.astimezone(tz)
            return now_utc.replace(tzinfo=None)

    monkeypatch.setattr(symbol_validation, "datetime", _Congelado)


def _simbolo_inexistente_binance(request: httpx.Request) -> httpx.Response:
    """Lote e 1-a-1 da Binance recusam com 400 -1121 (símbolo inválido)."""
    return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})


def test_post_cripto_valido_cadastra_201_e_normaliza(settings, capsys, monkeypatch):
    """POST normaliza para MAIÚSCULAS (D-05), valida na fonte e devolve o item."""
    client = _admin_logado(settings, capsys)
    requests: list[httpx.Request] = []

    def binance_ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"symbol": "SOLUSDT", "price": "96.5"}])

    _injeta_transporte(monkeypatch, binance_ok, requests)
    resposta = client.post(
        "/api/watch",
        json={"symbol": " solusdt ", "market": "crypto", "target_below": 90.0},
    )
    assert resposta.status_code == 201
    assert resposta.json() == {
        "symbol": "SOLUSDT",
        "market": "crypto",
        "target_below": 90.0,
        "paused": False,  # cadastro nasce ativo (a pausa é exclusivo do PATCH, WATCH-03)
        "currency": "US$",
        "price": None,
        "price_at": None,
        "change_pct": None,
        "alerted": False,
        "alerted_at": None,
        "notify_state": None,
        "fresh": "none",  # D-02 (05-01): ciclo novo sem cotação é "none" no servidor
        "suspect": False,
    }
    assert json.loads(requests[0].url.params["symbols"]) == ["SOLUSDT"]  # normalizado
    conn = _conn(settings)
    try:
        linha = conn.execute("SELECT * FROM quote WHERE symbol = 'SOLUSDT'").fetchone()
        assert linha is not None
        assert linha["alerted"] == 0 and linha["notify_state"] is None  # ciclo NOVO (D-02)
    finally:
        conn.close()


def test_post_b3_fora_do_pregao_aceita_sem_chamar_a_brapi(settings, capsys, monkeypatch):
    """D-03: fora do pregão o cadastro B3 é aceito SEM rede — a 1ª rodada valida."""
    client = _admin_logado(settings, capsys)
    _congela_relogio(monkeypatch, _DOMINGO)
    requests: list[httpx.Request] = []
    _injeta_transporte(monkeypatch, _violento, requests)  # levantaria se chamado
    resposta = client.post(
        "/api/watch",
        json={"symbol": "petr4", "market": "b3", "target_below": 38.5},
    )
    assert resposta.status_code == 201
    assert requests == []  # prova: nenhuma chamada saiu
    symbols = [i["symbol"] for i in client.get("/api/watch").json()["itens"]]
    assert symbols == ["PETR4"]  # D-05: normalizado para maiúsculas


def test_post_b3_em_pregao_inexistente_recusa_422(settings, capsys, monkeypatch):
    """Em pregão, símbolo B3 inexistente (brapi 404) → 422 nomeando o ticker."""
    client = _admin_logado(settings, capsys)
    _congela_relogio(monkeypatch, _QUARTA_16H)
    requests: list[httpx.Request] = []

    def brapi_404(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "brapi.dev"
        return httpx.Response(404, json={"message": "not found"})

    _injeta_transporte(monkeypatch, brapi_404, requests)
    resposta = client.post(
        "/api/watch",
        json={"symbol": "nope4", "market": "b3", "target_below": 10.0},
    )
    assert resposta.status_code == 422
    assert resposta.json() == {
        "erros": [
            {"symbol": "NOPE4", "campo": "symbol", "problema": "símbolo não encontrado na fonte"}
        ]
    }
    conn = _conn(settings)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"] == 0
    finally:
        conn.close()


def test_post_cripto_inexistente_recusa_422(settings, capsys, monkeypatch):
    """Cripto inexistente (400 -1121 da Binance) → 422 nomeando o símbolo."""
    client = _admin_logado(settings, capsys)
    requests: list[httpx.Request] = []
    _injeta_transporte(monkeypatch, _simbolo_inexistente_binance, requests)
    resposta = client.post(
        "/api/watch",
        json={"symbol": "BOGUS", "market": "crypto", "target_below": 1.0},
    )
    assert resposta.status_code == 422
    assert resposta.json() == {
        "erros": [
            {"symbol": "BOGUS", "campo": "symbol", "problema": "símbolo não encontrado na fonte"}
        ]
    }
    conn = _conn(settings)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"] == 0
    finally:
        conn.close()


def test_post_duplicado_recusa_422_sem_tocar_o_banco(settings, capsys, monkeypatch):
    """D-05: 'petr4' e 'PETR4' são o MESMO item — duplicado é recusa local, sem rede."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
    finally:
        conn.close()
    requests: list[httpx.Request] = []
    _injeta_transporte(monkeypatch, _violento, requests)
    resposta = client.post(
        "/api/watch",
        json={"symbol": "petr4", "market": "b3", "target_below": 20.0},
    )
    assert resposta.status_code == 422
    assert resposta.json() == {
        "erros": [{"symbol": "PETR4", "campo": "symbol", "problema": "o símbolo já está na lista"}]
    }
    assert requests == []  # duplicado por SELECT ANTES de qualquer rede
    conn = _conn(settings)
    try:
        linha = conn.execute("SELECT target_below FROM watch WHERE symbol = 'PETR4'").fetchone()
        assert linha["target_below"] == 38.5  # banco intocado
        assert conn.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"] == 1
    finally:
        conn.close()


@pytest.mark.parametrize("alvo", [0, -1.5])
def test_post_alvo_invalido_recusa_422(alvo, settings, capsys):
    """Alvo <= 0 → 422 envelope D-08 com campo e problema exatos (SC-4)."""
    client = _admin_logado(settings, capsys)
    resposta = client.post(
        "/api/watch",
        json={"symbol": "PETR4", "market": "b3", "target_below": alvo},
    )
    assert resposta.status_code == 422
    assert resposta.json() == {
        "erros": [
            {
                "symbol": "PETR4",
                "campo": "target_below",
                "problema": "o alvo deve ser maior que zero",
            }
        ]
    }


def test_alvo_infinito_via_1e999_recusado_422_no_post_put_e_patch(settings, capsys, monkeypatch):
    """WR-04: JSON 1e999 vira +Infinity no float e o pydantic ACEITAVA (default
    allow_inf_nan=True) — o 201 criava item com alvo inf que o GET devolvia como
    null (número obrigatório do contrato congelado) e o worker nunca alertava.
    POST, PUT e PATCH recusam com 422 no envelope D-08 nomeando target_below.
    B3 fora do pregão + transporte violento: a recusa é de FORMA no pydantic,
    antes de qualquer fonte — zero rede nas duas fases do ciclo.
    """
    client = _admin_logado(settings, capsys)
    _congela_relogio(monkeypatch, _DOMINGO)  # fora do pregão: b3 sem rede
    requests: list[httpx.Request] = []
    _injeta_transporte(monkeypatch, _violento, requests)  # levantaria se chamado
    cabecalho = {"Content-Type": "application/json"}
    post = client.post(
        "/api/watch",
        content=b'{"symbol": "petr4", "market": "b3", "target_below": 1e999}',
        headers=cabecalho,
    )
    assert post.status_code == 422
    erro_post = post.json()["erros"][0]
    assert erro_post["symbol"] == "PETR4"
    assert erro_post["campo"] == "target_below"
    conn = _conn(settings)
    try:
        # recusa não grava: o alvo inf nunca chega ao banco
        assert conn.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"] == 0
    finally:
        conn.close()
    put = client.put(
        "/api/watch",
        content=b'{"itens": [{"symbol": "petr4", "market": "b3", "target_below": 1e999}]}',
        headers=cabecalho,
    )
    assert put.status_code == 422
    assert put.json()["erros"][0]["campo"] == "target_below"
    patch = client.patch(
        "/api/watch/PETR4/b3",
        content=b'{"target_below": 1e999}',
        headers=cabecalho,
    )
    assert patch.status_code == 422
    assert patch.json()["erros"][0]["campo"] == "target_below"
    assert requests == []  # prova: nenhuma fonte foi chamada nas três pernas


def test_post_market_invalido_fala_o_dialeto_do_envelope(settings, capsys):
    """Erro de FORMA do pydantic sai no MESMO envelope D-08 (handler em main.py)."""
    client = _admin_logado(settings, capsys)
    resposta = client.post(
        "/api/watch",
        json={"symbol": "PETR4", "market": "acoes", "target_below": 10.0},
    )
    assert resposta.status_code == 422
    assert resposta.json() == {
        "erros": [
            {"symbol": "PETR4", "campo": "market", "problema": "market deve ser b3 ou crypto"}
        ]
    }


def test_falha_de_rede_na_validacao_responde_503_nunca_422(settings, capsys, monkeypatch):
    """D-04: rede/timeout → 503 'tente de novo' — nunca disfarçado de 422."""
    client = _admin_logado(settings, capsys)
    requests: list[httpx.Request] = []

    def caindo(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _injeta_transporte(monkeypatch, caindo, requests)
    resposta = client.post(
        "/api/watch",
        json={"symbol": "BTCUSDT", "market": "crypto", "target_below": 100.0},
    )
    assert resposta.status_code == 503
    assert resposta.json() == {
        "detail": "fonte indisponível para validar o símbolo — tente de novo"
    }
    conn = _conn(settings)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"] == 0
    finally:
        conn.close()


def test_token_da_brapi_nao_vaza_na_recusa_de_validacao(settings, capsys, monkeypatch):
    """T-04-08: falha de transporte com token na query — o valor NUNCA aparece."""
    marcador = "segredo-brapi-nao-vazar"
    com_token = replace(settings, brapi_token=marcador)
    client = _admin_logado(com_token, capsys)
    _congela_relogio(monkeypatch, _QUARTA_16H)  # em pregão: a brapi seria chamada
    requests: list[httpx.Request] = []

    def caindo(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    _injeta_transporte(monkeypatch, caindo, requests)
    resposta = client.post(
        "/api/watch",
        json={"symbol": "PETR4", "market": "b3", "target_below": 30.0},
    )
    assert resposta.status_code == 503
    assert marcador not in resposta.text
    assert marcador not in capsys.readouterr().out  # K2: nem no log


def test_patch_muda_o_alvo_e_normaliza_o_caminho(settings, capsys):
    """PATCH /api/watch/petr4/b3 → 200 com o alvo novo; caminho normalizado (D-05)."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
    finally:
        conn.close()
    resposta = client.patch("/api/watch/petr4/b3", json={"target_below": 30})
    assert resposta.status_code == 200
    assert resposta.json()["symbol"] == "PETR4"
    assert resposta.json()["target_below"] == 30.0
    lista = client.get("/api/watch").json()["itens"]
    assert lista[0]["target_below"] == 30.0


def test_patch_alvo_invalido_recusa_422(settings, capsys):
    """PATCH herda o gt=0 do modelo e o dialeto D-08 do handler."""
    client = _admin_logado(settings, capsys)
    resposta = client.patch("/api/watch/PETR4/b3", json={"target_below": 0})
    assert resposta.status_code == 422
    assert resposta.json()["erros"][0]["campo"] == "target_below"


def test_patch_de_inexistente_responde_404(settings, capsys):
    """PATCH de item fora da lista → 404 com detail fixo, sem alterar nada."""
    client = _admin_logado(settings, capsys)
    resposta = client.patch("/api/watch/NOPE4/b3", json={"target_below": 30})
    assert resposta.status_code == 404
    assert resposta.json() == {"detail": "item não encontrado"}


# --- 04-03 Task 2: pausa por item via PATCH (WATCH-03, D-09) — o lado web


def test_patch_pausa_item_e_o_mantem_visivel_na_lista(settings, capsys):
    """WATCH-03: pausar NÃO esconde o item — a lista NÃO encolhe e o preço congela."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.2,
            price_at="2026-10-28T16:00:00Z",
            change_pct=-1.2,
        )
    finally:
        conn.close()
    resposta = client.patch("/api/watch/PETR4/b3", json={"paused": True})
    assert resposta.status_code == 200
    assert resposta.json()["paused"] is True
    itens = client.get("/api/watch").json()["itens"]
    assert len(itens) == 1  # nunca lista oculta: o pausado fica na MESMA lista
    assert itens[0]["paused"] is True
    assert itens[0]["price"] == 35.2  # cotação congelada: o MESMO preço de antes
    assert itens[0]["price_at"] == "2026-10-28T16:00:00Z"


def test_patch_retoma_item_pausado(settings, capsys):
    """paused=false de volta: 200 e o GET reflete o estado ativo."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        conn.execute("UPDATE watch SET paused = 1 WHERE symbol = 'PETR4'")
    finally:
        conn.close()
    resposta = client.patch("/api/watch/petr4/b3", json={"paused": False})
    assert resposta.status_code == 200
    assert resposta.json()["paused"] is False
    assert resposta.json()["target_below"] == 38.5  # só a pausa mudou
    itens = client.get("/api/watch").json()["itens"]
    assert [i["paused"] for i in itens] == [False]


def test_patch_muda_alvo_e_pausa_na_mesma_write_tx(settings, capsys):
    """PATCH {"target_below": 30, "paused": true} → 200 mudando os dois de uma vez."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
    finally:
        conn.close()
    resposta = client.patch("/api/watch/PETR4/b3", json={"target_below": 30, "paused": True})
    assert resposta.status_code == 200
    assert resposta.json()["target_below"] == 30.0
    assert resposta.json()["paused"] is True


def test_patch_pausa_repetida_e_idempotente(settings, capsys):
    """Pausar item já pausado: 200, estado permanece, nenhuma outra coluna reescrita."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        conn.execute("UPDATE watch SET paused = 1 WHERE symbol = 'PETR4'")
    finally:
        conn.close()
    resposta = client.patch("/api/watch/PETR4/b3", json={"paused": True})
    assert resposta.status_code == 200
    conn = _conn(settings)
    try:
        linha = conn.execute(
            "SELECT target_below, paused FROM watch WHERE symbol = 'PETR4'"
        ).fetchone()
    finally:
        conn.close()
    assert linha["paused"] == 1  # estado permanece
    assert linha["target_below"] == 38.5  # nada foi reescrito


def test_patch_vazio_recusa_422_envelope(settings, capsys):
    """PATCH sem nenhum campo → 422 no envelope D-08 (ao menos um campo)."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
    finally:
        conn.close()
    resposta = client.patch("/api/watch/PETR4/b3", json={})
    assert resposta.status_code == 422
    corpo = resposta.json()
    assert set(corpo) == {"erros"} and corpo["erros"]
    assert corpo["erros"][0]["problema"] == "informe ao menos um campo para atualizar"


def test_patch_paused_nao_booleano_recusa_422(settings, capsys):
    """PATCH {"paused": "sim"} → 422 envelope: o valor deve ser verdadeiro ou falso."""
    client = _admin_logado(settings, capsys)
    resposta = client.patch("/api/watch/PETR4/b3", json={"paused": "sim"})
    assert resposta.status_code == 422
    erro = resposta.json()["erros"][0]
    assert erro["campo"] == "paused"
    assert erro["problema"] == "o valor deve ser verdadeiro ou falso"


def test_patch_pausa_nao_toca_estado_de_alerta_da_quote(settings, capsys):
    """Pausar não toca quote: alerted/alerted_at/alerted_target/notify_state intactos."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.2,
            price_at="2026-10-28T16:00:00Z",
            change_pct=-1.2,
            alerted=1,
            alerted_at="2026-10-28T16:05:00Z",
            alerted_target=38.5,
            notify_state='{"telegram": "ok"}',
        )
    finally:
        conn.close()
    assert client.patch("/api/watch/PETR4/b3", json={"paused": True}).status_code == 200
    conn = _conn(settings)
    try:
        linha = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    finally:
        conn.close()
    assert linha["alerted"] == 1  # nada do estado de alerta é resetado pela pausa
    assert linha["alerted_at"] == "2026-10-28T16:05:00Z"
    assert linha["alerted_target"] == 38.5
    assert linha["notify_state"] == '{"telegram": "ok"}'
    assert linha["price"] == 35.2 and linha["price_at"] == "2026-10-28T16:00:00Z"


def test_patch_de_inexistente_com_paused_responde_404(settings, capsys):
    """Pausar item fora da lista → 404, nada alterado (mesmo dialeto do PATCH de alvo)."""
    client = _admin_logado(settings, capsys)
    resposta = client.patch("/api/watch/NOPE4/b3", json={"paused": True})
    assert resposta.status_code == 404
    assert resposta.json() == {"detail": "item não encontrado"}


def test_delete_remove_a_watch_204_e_patch_seguinte_404(settings, capsys):
    """DELETE → 204 e a linha de watch some; o PATCH seguinte confirma a remoção."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(conn, "PETR4", "b3", price=35.0, alerted=1)
    finally:
        conn.close()
    # SEM corpo — a forma que o contrato congelado declara: DELETE sem
    # requestBody no openapi.json, e o guard só exige JSON em mutação COM
    # corpo (WR-01, opção (a) do 04-05)
    resposta = client.delete("/api/watch/PETR4/b3")
    assert resposta.status_code == 204
    assert resposta.content == b""
    conn = _conn(settings)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"] == 0
    finally:
        conn.close()
    depois = client.patch("/api/watch/PETR4/b3", json={"target_below": 30})
    assert depois.status_code == 404


def test_delete_de_inexistente_responde_404(settings, capsys):
    """DELETE de item fora da lista → 404, nada apagado."""
    client = _admin_logado(settings, capsys)
    resposta = client.delete("/api/watch/NOPE4/b3")
    assert resposta.status_code == 404
    assert resposta.json() == {"detail": "item não encontrado"}


def test_recriar_apos_delete_comeca_ciclo_novo(settings, capsys, monkeypatch):
    """D-02: recriar sobreõe a quote — alerted=0 e histórico de alerta NULL.

    A quarentena da Fase 2 (last_fetch_error='not_found') é limpa por aqui:
    o estado de alerta nunca atravessa uma remoção.
    """
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.0,
            alerted=1,
            alerted_at="2026-10-28T16:05:00Z",
            alerted_target=38.5,
            notify_state='{"telegram": "ok"}',
        )
        conn.execute("UPDATE quote SET last_fetch_error = 'not_found' WHERE symbol = 'PETR4'")
    finally:
        conn.close()
    # sem corpo: a forma que o contrato congelado declara (WR-01)
    assert client.delete("/api/watch/PETR4/b3").status_code == 204
    _congela_relogio(monkeypatch, _DOMINGO)  # fora do pregão: 201 sem rede
    requests: list[httpx.Request] = []
    _injeta_transporte(monkeypatch, _violento, requests)
    resposta = client.post(
        "/api/watch",
        json={"symbol": "petr4", "market": "b3", "target_below": 38.5},
    )
    assert resposta.status_code == 201
    conn = _conn(settings)
    try:
        linha = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    finally:
        conn.close()
    assert linha["alerted"] == 0
    assert linha["alerted_at"] is None
    assert linha["alerted_target"] is None
    assert linha["notify_state"] is None
    assert linha["last_fetch_error"] is None  # quarentena da Fase 2 limpa


# --- Task 3: PUT /api/watch em lote — tudo-ou-nada (D-06), PUT nunca remove
# --- (D-07), envelope completo com todos os erros acumulados


def test_put_cria_e_edita_e_preserva_ausentes(settings, capsys, monkeypatch):
    """D-06/D-07: lote cria o novo, edita o existente e NUNCA remove ausentes."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_watch(conn, "VALE3", "b3", 60.0)
        _seed_watch(conn, "BTCUSDT", "crypto", 350000.0)
    finally:
        conn.close()
    _congela_relogio(monkeypatch, _DOMINGO)  # novo b3: fora do pregão, sem rede
    requests: list[httpx.Request] = []
    _injeta_transporte(monkeypatch, _violento, requests)
    resposta = client.put(
        "/api/watch",
        json={
            "itens": [
                {"symbol": "itub4", "market": "b3", "target_below": 33.0},
                {"symbol": "petr4", "market": "b3", "target_below": 30.0},
            ]
        },
    )
    assert resposta.status_code == 200
    assert requests == []  # novo B3 fora do pregão: validação D-03 sem rede
    apos = client.get("/api/watch").json()
    assert resposta.json() == apos  # MESMA forma do GET (a Fase 5 re-renderiza)
    por_simbolo = {i["symbol"]: i for i in apos["itens"]}
    assert set(por_simbolo) == {"PETR4", "VALE3", "BTCUSDT", "ITUB4"}  # D-07
    assert por_simbolo["PETR4"]["target_below"] == 30.0  # editado no lote
    assert por_simbolo["ITUB4"]["currency"] == "R$"


def test_put_item_invalido_422_e_nada_muda(settings, capsys):
    """D-06/SC-4: 1 item falho → 422 com o envelope e NADA muda (contagem igual)."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_watch(conn, "VALE3", "b3", 60.0)
    finally:
        conn.close()
    resposta = client.put(
        "/api/watch",
        json={
            "itens": [
                {"symbol": "PETR4", "market": "b3", "target_below": 30.0},
                {"symbol": "VALE3", "market": "b3", "target_below": 0},
            ]
        },
    )
    assert resposta.status_code == 422
    assert resposta.json() == {
        "erros": [
            {
                "symbol": "VALE3",
                "campo": "target_below",
                "problema": "o alvo deve ser maior que zero",
            }
        ]
    }
    conn = _conn(settings)
    try:
        # Prova de atomicidade por CONTAGEM de linhas, não só por status:
        assert conn.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"] == 2
        assert conn.execute("SELECT COUNT(*) AS n FROM quote").fetchone()["n"] == 0
        alvo = conn.execute("SELECT target_below FROM watch WHERE symbol = 'PETR4'").fetchone()
        assert alvo["target_below"] == 38.5  # a edição válida do corpo NÃO vazou
    finally:
        conn.close()


def test_put_duplicado_dentro_do_corpo_422(settings, capsys, monkeypatch):
    """'PETR4' e 'petr4' no MESMO corpo → 422 nomeando os dois, nada gravado."""
    client = _admin_logado(settings, capsys)
    _congela_relogio(monkeypatch, _DOMINGO)
    requests: list[httpx.Request] = []
    _injeta_transporte(monkeypatch, _violento, requests)
    resposta = client.put(
        "/api/watch",
        json={
            "itens": [
                {"symbol": "PETR4", "market": "b3", "target_below": 10.0},
                {"symbol": "petr4", "market": "b3", "target_below": 20.0},
            ]
        },
    )
    assert resposta.status_code == 422
    corpo = resposta.json()
    assert [e["symbol"] for e in corpo["erros"]] == ["PETR4", "PETR4"]  # os DOIS
    assert all(e["problema"] == "símbolo repetido no lote" for e in corpo["erros"])
    assert requests == []  # recusa local: nenhuma rede
    conn = _conn(settings)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM watch").fetchone()["n"] == 0
    finally:
        conn.close()


def test_put_novo_inexistente_422_e_existentes_nao_revalidam(settings, capsys, monkeypatch):
    """Novo inexistente → 422 e nada gravado; EXISTENTES do corpo não vão à rede."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
    finally:
        conn.close()
    requests: list[httpx.Request] = []
    _injeta_transporte(monkeypatch, _simbolo_inexistente_binance, requests)
    resposta = client.put(
        "/api/watch",
        json={
            "itens": [
                {"symbol": "petr4", "market": "b3", "target_below": 35.0},
                {"symbol": "BOGUS", "market": "crypto", "target_below": 1.0},
            ]
        },
    )
    assert resposta.status_code == 422
    assert resposta.json() == {
        "erros": [
            {"symbol": "BOGUS", "campo": "symbol", "problema": "símbolo não encontrado na fonte"}
        ]
    }
    for requisicao in requests:  # a rede só viu o NOVO — nunca o existente
        assert "PETR4" not in (requisicao.url.params.get("symbols") or "")
    conn = _conn(settings)
    try:
        alvo = conn.execute("SELECT target_below FROM watch WHERE symbol = 'PETR4'").fetchone()
        assert alvo["target_below"] == 38.5  # all-or-nothing: o válido não vazou
    finally:
        conn.close()


def test_put_repetido_idempotente(settings, capsys, monkeypatch):
    """PUT repetido com o MESMO corpo deixa a lista idêntica — zero rede na 2ª."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
    finally:
        conn.close()
    _congela_relogio(monkeypatch, _DOMINGO)
    requests: list[httpx.Request] = []

    def binance_ok(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"symbol": "BTCUSDT", "price": "300000.0"}])

    _injeta_transporte(monkeypatch, binance_ok, requests)
    corpo = {
        "itens": [
            {"symbol": "petr4", "market": "b3", "target_below": 30.0},
            {"symbol": "BTCUSDT", "market": "crypto", "target_below": 300000.0},
        ]
    }
    primeira = client.put("/api/watch", json=corpo)
    assert primeira.status_code == 200
    apos_primeira = client.get("/api/watch").json()
    rede_na_primeira = len(requests)
    segunda = client.put("/api/watch", json=corpo)
    assert segunda.status_code == 200
    assert client.get("/api/watch").json() == apos_primeira  # efeito idempotente
    assert len(requests) == rede_na_primeira  # 2ª passada: tudo existente, sem rede


def test_put_item_removido_renasce_ciclo_novo(settings, capsys, monkeypatch):
    """Item removido via DELETE e re-enviado no PUT nasce com ciclo NOVO (D-02)."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.0,
            alerted=1,
            alerted_at="2026-10-28T16:05:00Z",
            alerted_target=38.5,
            notify_state='{"telegram": "ok"}',
        )
    finally:
        conn.close()
    # sem corpo: a forma que o contrato congelado declara (WR-01)
    assert client.delete("/api/watch/PETR4/b3").status_code == 204
    _congela_relogio(monkeypatch, _DOMINGO)
    requests: list[httpx.Request] = []
    _injeta_transporte(monkeypatch, _violento, requests)
    resposta = client.put(
        "/api/watch",
        json={"itens": [{"symbol": "petr4", "market": "b3", "target_below": 38.5}]},
    )
    assert resposta.status_code == 200
    conn = _conn(settings)
    try:
        linha = conn.execute("SELECT * FROM quote WHERE symbol = 'PETR4'").fetchone()
    finally:
        conn.close()
    assert linha is not None
    assert linha["alerted"] == 0  # mesmo reset da D-02 do POST
    assert linha["alerted_at"] is None
    assert linha["alerted_target"] is None
    assert linha["notify_state"] is None
    assert linha["last_fetch_error"] is None


# --- 05-01 Task 1: fresh/suspect derivados NO SERVIDOR no GET /api/watch (D-02)
# --- os MESMOS quatro estados de freshness_state e a borda inclusiva da
# --- suspeita — o navegador recebe o estado pronto, nunca os limiares


def test_get_quote_fresca_em_pregao_fresh_ok(settings, capsys, monkeypatch):
    """Idade dentro do limite e B3 em pregão → fresh "ok" (molde freshness_state)."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.2,
            price_at="2026-10-28T15:45:00Z",  # 15 min antes do relógio congelado
        )
    finally:
        conn.close()
    _injeta_relogio_watch(monkeypatch, _QUARTA_16H)
    item = client.get("/api/watch").json()["itens"][0]
    assert item["fresh"] == "ok"
    assert item["suspect"] is False


def test_get_quote_velha_em_pregao_fresh_stale(settings, capsys, monkeypatch):
    """Idade além de STALE_MINUTES_B3 (75) DENTRO do pregão → fresh "stale"."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.2,
            price_at="2026-10-28T14:30:00Z",  # 90 min antes: acima do limite
        )
    finally:
        conn.close()
    _injeta_relogio_watch(monkeypatch, _QUARTA_16H)
    assert client.get("/api/watch").json()["itens"][0]["fresh"] == "stale"


def test_get_b3_fora_do_pregao_fresh_closed_prevalece_sobre_stale(settings, capsys, monkeypatch):
    """Precedência de freshness_state: B3 fechada (domingo) → "closed", não "stale"."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.2,
            price_at="2026-10-28T14:30:00Z",  # velha E fora do pregão
        )
    finally:
        conn.close()
    _injeta_relogio_watch(monkeypatch, _DOMINGO)  # SC-1: pregão fechado
    assert client.get("/api/watch").json()["itens"][0]["fresh"] == "closed"


def test_get_cripto_velha_fora_do_pregao_fresh_stale_nunca_closed(settings, capsys, monkeypatch):
    """Cripto nunca é closed (mercado 24/7): velha no domingo segue "stale"."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "BTCUSDT", "crypto", 350000.0)
        _seed_quote(
            conn,
            "BTCUSDT",
            "crypto",
            price=300000.0,
            price_at="2026-10-28T14:30:00Z",  # > STALE_MINUTES_CRYPTO (30)
        )
    finally:
        conn.close()
    _injeta_relogio_watch(monkeypatch, _DOMINGO)
    assert client.get("/api/watch").json()["itens"][0]["fresh"] == "stale"


def test_get_item_sem_cotacao_fresh_none(settings, capsys, monkeypatch):
    """Sem linha de quote (price/price_at NULL) → fresh "none", nunca erro."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "VALE3", "b3", 60.0)
    finally:
        conn.close()
    _injeta_relogio_watch(monkeypatch, _QUARTA_16H)
    item = client.get("/api/watch").json()["itens"][0]
    assert item["fresh"] == "none"
    assert item["suspect"] is False


@pytest.mark.parametrize(
    ("change_pct", "suspeito"),
    [
        (20.0, True),  # borda INCLUSIVA: abs(change_pct) == SUSPECT_PCT (padrão 20)
        (-20.0, True),  # queda brusca também suspeita (módulo da variação)
        (19.9, False),  # abaixo do limiar: normal
        (-1.2, False),
    ],
)
def test_get_suspect_borda_inclusiva_no_limiar(change_pct, suspeito, settings, capsys, monkeypatch):
    """Mesmo limiar e borda INCLUSIVA de messages.suspect_note (D-10): abs ≥ SUSPECT_PCT."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.2,
            price_at="2026-10-28T15:45:00Z",
            change_pct=change_pct,
        )
    finally:
        conn.close()
    _injeta_relogio_watch(monkeypatch, _QUARTA_16H)
    assert client.get("/api/watch").json()["itens"][0]["suspect"] is suspeito


def test_get_change_pct_nulo_suspect_false(settings, capsys, monkeypatch):
    """change_pct NULL (fonte não fornece) → suspect False, sem TypeError."""
    client = _admin_logado(settings, capsys)
    conn = _conn(settings)
    try:
        _seed_watch(conn, "PETR4", "b3", 38.5)
        _seed_quote(
            conn,
            "PETR4",
            "b3",
            price=35.2,
            price_at="2026-10-28T15:45:00Z",
            change_pct=None,
        )
    finally:
        conn.close()
    _injeta_relogio_watch(monkeypatch, _QUARTA_16H)
    item = client.get("/api/watch").json()["itens"][0]
    assert item["suspect"] is False
    assert item["fresh"] == "ok"  # o frescor NÃO depende da variação
