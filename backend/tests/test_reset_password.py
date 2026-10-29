"""Testes do comando de redefinição de senha (AUTH-03, SC-3 — plano 04-04).

O reset é a saída de emergência do administrador sem acesso à sessão: troca a
senha e encerra TODAS as sessões do usuário na MESMA write_tx (T-04-12) —
depois do reset, nenhum cookie antigo autentica. A senha nova entra por
``getpass`` (nunca ``sys.argv`` — não vaza para o histórico do shell) e nunca
aparece em log nem na saída do comando (K2). Usuário inexistente é erro limpo
que não cria usuário nem apaga sessão alguma (borda da sonda). Zero rede:
banco real em tmp_path e TestClient (molde test_auth_api.py).
"""

from __future__ import annotations

import getpass
import sys
from datetime import UTC, datetime, timedelta

import pytest
from baixou.core.db import WORKER_TIMEOUT_S, connect, write_tx
from baixou.core.migrations import apply_migrations
from baixou.web import reset_password
from baixou.web.main import create_app
from baixou.web.passwords import hash_password, new_token, token_digest, verify_password
from fastapi.testclient import TestClient

SENHA_ANTIGA = "senha-antiga-segura-01"
SENHA_NOVA = "senha-nova-segura-02"
SENHA_CURTA = "curta-senha"  # 11 caracteres — abaixo do mínimo de 12
SENHA_OUTRA = "senha-diferente-03"


def _cria_admin_e_sessoes(settings, senha: str, sessoes: int = 2) -> list[str]:
    """Admin + N sessões vivas criados DIRETO no banco de teste (sem HTTP)."""
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        with write_tx(conn):
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                ("admin", hash_password(senha), "2026-01-01T00:00:00Z"),
            )
            user_id = cursor.lastrowid
        futuro = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        tokens = []
        for _ in range(sessoes):
            token = new_token()
            with write_tx(conn):
                conn.execute(
                    "INSERT INTO sessions "
                    "(token_hash, user_id, created_at, expires_at, last_used_at, remember) "
                    "VALUES (?, ?, ?, ?, NULL, 0)",
                    (token_digest(token), user_id, "2026-01-01T00:00:00Z", futuro),
                )
            tokens.append(token)
    finally:
        conn.close()
    return tokens


def _roda_reset(monkeypatch, respostas: list[str], *args: str) -> None:
    """Roda main() com getpass alimentado por uma fila e argv sob controle.

    A senha SÓ entra pela fila do getpass — nenhum teste entrega senha por
    argv (o comando não aceita: K2/histórico de shell).
    """
    fila = iter(respostas)
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(fila))
    monkeypatch.setattr(
        sys, "argv", ["python", "-m", "baixou.web.reset_password", *args]
    )
    reset_password.main()


def _contagem(settings, tabela: str) -> int:
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        return conn.execute(f"SELECT COUNT(*) AS n FROM {tabela}").fetchone()["n"]
    finally:
        conn.close()


def _hash_do_admin(settings) -> str:
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = 'admin'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "usuário admin desapareceu do banco de teste"
    return row["password_hash"]


def test_reset_troca_a_senha_e_mata_todas_as_sessoes_na_mesma_write_tx(
    settings, monkeypatch
):
    """Duas sessões vivas → reset apaga as DUAS linhas e troca o hash (T-04-12)."""
    tokens = _cria_admin_e_sessoes(settings, SENHA_ANTIGA, sessoes=2)
    assert _contagem(settings, "sessions") == 2
    _roda_reset(monkeypatch, [SENHA_NOVA, SENHA_NOVA])
    hash_novo = _hash_do_admin(settings)
    assert verify_password(SENHA_NOVA, hash_novo)
    assert not verify_password(SENHA_ANTIGA, hash_novo)
    # as DUAS linhas de sessão morreram JUNTAS com o UPDATE da senha
    assert _contagem(settings, "sessions") == 0
    # e nenhum cookie antigo autentica mais (401, molde do tracer 04-01)
    with TestClient(create_app(settings)) as client:
        for token in tokens:
            resposta = client.get("/api/watch", cookies={"baixou_session": token})
            assert resposta.status_code == 401


def test_apos_o_reset_login_com_a_senha_nova_passa_e_a_antiga_falha(
    settings, monkeypatch
):
    _cria_admin_e_sessoes(settings, SENHA_ANTIGA, sessoes=1)
    _roda_reset(monkeypatch, [SENHA_NOVA, SENHA_NOVA])
    with TestClient(create_app(settings)) as client:
        antigo = client.post(
            "/api/auth/login", json={"username": "admin", "password": SENHA_ANTIGA}
        )
        novo = client.post(
            "/api/auth/login", json={"username": "admin", "password": SENHA_NOVA}
        )
        assert antigo.status_code == 401
        assert novo.status_code == 200
        # a sessão aberta com a senha nova acessa a rota protegida
        assert client.get("/api/watch").status_code == 200


def test_senha_curta_recusada_sem_alterar_nada(settings, monkeypatch, capsys):
    """11 caracteres → recusa nomeando o mínimo; hash e sessões intactos."""
    _cria_admin_e_sessoes(settings, SENHA_ANTIGA, sessoes=2)
    with pytest.raises(SystemExit) as saida:
        _roda_reset(monkeypatch, [SENHA_CURTA, SENHA_CURTA])
    assert saida.value.code != 0
    assert "pelo menos 12 caracteres" in capsys.readouterr().out
    assert verify_password(SENHA_ANTIGA, _hash_do_admin(settings))  # hash intacto
    assert _contagem(settings, "sessions") == 2  # nenhuma sessão morreu


def test_senhas_diferentes_recusadas_sem_alterar_nada(settings, monkeypatch, capsys):
    _cria_admin_e_sessoes(settings, SENHA_ANTIGA, sessoes=2)
    with pytest.raises(SystemExit) as saida:
        _roda_reset(monkeypatch, [SENHA_NOVA, SENHA_OUTRA])
    assert saida.value.code != 0
    assert "as senhas não coincidem" in capsys.readouterr().out
    assert verify_password(SENHA_ANTIGA, _hash_do_admin(settings))  # hash intacto
    assert _contagem(settings, "sessions") == 2  # nenhuma sessão morreu


def test_usuario_inexistente_nao_cria_nem_apaga_nada(settings, monkeypatch, capsys):
    """Borda da sonda: erro nomeando o username, SEM tocar em usuário algum."""
    _cria_admin_e_sessoes(settings, SENHA_ANTIGA, sessoes=2)
    with pytest.raises(SystemExit) as saida:
        _roda_reset(monkeypatch, [SENHA_NOVA, SENHA_NOVA], "--username", "ghost")
    assert saida.value.code != 0
    assert "usuário não encontrado: ghost" in capsys.readouterr().out
    assert _contagem(settings, "users") == 1  # nada foi criado
    assert _contagem(settings, "sessions") == 2  # sessões de ninguém foram apagadas


def test_senha_e_hash_nunca_aparecem_na_saida_do_comando(settings, monkeypatch, capsys):
    """K2/T-04-13: a saída tem o evento de log e NUNCA a senha nem o hash."""
    _cria_admin_e_sessoes(settings, SENHA_ANTIGA, sessoes=1)
    _roda_reset(monkeypatch, [SENHA_NOVA, SENHA_NOVA])
    saida = capsys.readouterr().out
    assert "password_reset" in saida  # o evento de log existe
    assert SENHA_NOVA not in saida  # a senha digitada não vaza na saída
    assert _hash_do_admin(settings) not in saida  # nem o hash novo
