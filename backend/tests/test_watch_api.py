"""API da lista (plano 04-02): GET com cotação e moeda, CRUD item a item e PUT em lote.

Sem rede e com banco real em tmp_path (molde test_auth_api.py): usuário e
sessão criados PELAS ROTAS de auth, itens semeados direto no banco (molde
seed_watch de test_round.py). A forma do item é provada por IGUALDADE EXATA
de dicionário (precedente 03-05: igualdade chave-a-chave deixa sobra em
silêncio) — o contrato que a Fase 5 consome com tipos gerados sai daqui
congelado (WATCH-01/WATCH-02, D-06/D-07/D-08).
"""

from __future__ import annotations

import sqlite3

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
        client.post(
            "/api/auth/login", json={"username": "admin", "password": SENHA}
        ).status_code
        == 200
    )
    return client


def _conn(settings) -> sqlite3.Connection:
    """Conexão de inspeção/semeadura fora do client (WAL: leitura concorrente é segura)."""
    return connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)


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
        (symbol, market, price, price_at, change_pct, alerted, alerted_at, alerted_target, notify_state),
    )


# --- Task 1: GET /api/watch completo — quote via LEFT JOIN e moeda (WATCH-02) ---


def test_get_devolve_itens_com_cotacao_e_moeda(settings, capsys):
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
    resposta = client.get("/api/watch")
    assert resposta.status_code == 200
    # Igualdade EXATA: a forma é contrato congelado para a Fase 5 (tipos gerados).
    assert resposta.json() == {
        "itens": [
            {
                "symbol": "PETR4",
                "market": "b3",
                "target_below": 38.5,
                "currency": "R$",
                "price": 35.2,
                "price_at": "2026-10-28T16:00:00Z",
                "change_pct": -1.2,
                "alerted": True,
                "alerted_at": "2026-10-28T16:05:00Z",
                "notify_state": {"telegram": "ok"},  # objeto, NUNCA string crua
            },
            {
                "symbol": "BTCUSDT",
                "market": "crypto",
                "target_below": 350000.0,
                "currency": "US$",
                "price": None,
                "price_at": None,
                "change_pct": None,
                "alerted": False,
                "alerted_at": None,
                "notify_state": None,
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
