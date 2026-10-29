"""Prova de concorrência do semáforo do scrypt (04-01 T8, AUTH-02) — backstop.

Cada hashing segura 16 MiB e ``_SCRYPT_SLOTS`` (threading.BoundedSemaphore(2),
em passwords.py) limita a DOIS hashings simultâneos. O login é ``def``
SÍNCRONO — o FastAPI o executa no threadpool do anyio, então a concorrência
REAL aqui é por THREADS: 6 threads disparam logins válidos ao MESMO tempo pelo
MESMO TestClient (o portal anyio do contexto é thread-safe por contrato) e
disputam o semáforo de verdade. Sem rede e com banco real em tmp_path (molde
test_auth_api.py): 6 credenciais válidas não podem 5xx, não podem 429 (não há
falha contada) e cada login abre a SUA sessão — 6 linhas com digests distintos.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from baixou.core.db import WEB_TIMEOUT_S, connect
from baixou.core.migrations import apply_migrations
from baixou.web.auth import ensure_setup_token
from baixou.web.main import create_app
from fastapi.testclient import TestClient

SENHA = "senha-super-segura-123"
LOGINS_SIMULTANEOS = 6


def _token_do_log(saida: str) -> str:
    for line in saida.splitlines():
        if "setup_token_created" in line:
            resto = line.split("token de primeiro acesso: ", 1)[1]
            return resto.split(" ", 1)[0].strip()
    raise AssertionError("evento setup_token_created ausente do log")


def test_seis_logins_validos_simultaneos_todos_200_e_sessoes_integras(settings, capsys):
    """6 logins válidos concorrentes: todos 200 e 6 sessões com digests distintos.

    Prova o backstop do semáforo do scrypt (04-01 T8): sob disputa real por
    threads (threadpool do anyio), o limite de 2 hashings simultâneos NUNCA
    vira erro para quem tem credencial válida — e o bloqueio AUTH-02 não
    dispara para logins que dão certo.
    """
    apply_migrations(settings.db_path)
    ensure_setup_token(settings)
    token_setup = _token_do_log(capsys.readouterr().out)
    with TestClient(create_app(settings)) as client:
        assert (
            client.post(
                "/api/setup",
                json={"token": token_setup, "username": "admin", "password": SENHA},
            ).status_code
            == 201
        )

        def loga(_: int) -> int:
            resposta = client.post(
                "/api/auth/login", json={"username": "admin", "password": SENHA}
            )
            return resposta.status_code

        with ThreadPoolExecutor(max_workers=LOGINS_SIMULTANEOS) as piscina:
            status = list(piscina.map(loga, range(LOGINS_SIMULTANEOS)))

    # Nenhum 5xx, nenhum 429: 6 logins VÁLIDOS não contam falha — o bloqueio
    # AUTH-02 não pode atrapalhar quem acerta a senha, mesmo concorrendo.
    assert status == [200] * LOGINS_SIMULTANEOS

    conn = connect(settings.db_path, timeout_s=WEB_TIMEOUT_S)
    try:
        linhas = conn.execute("SELECT token_hash FROM sessions").fetchall()
    finally:
        conn.close()
    # Sessões íntegras: UMA linha por login, NENHUM digest repetido (cada
    # sessão tem token próprio de 256 bits — nenhuma corrida perdeu INSERT).
    assert len(linhas) == LOGINS_SIMULTANEOS
    assert len({row["token_hash"] for row in linhas}) == LOGINS_SIMULTANEOS
