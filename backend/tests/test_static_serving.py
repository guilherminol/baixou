"""Estáticos do build Vite servidos pelo PRÓPRIO web (05-02) — sem depender de Node.

A fixture cria um dist FALSO em tmp_path (index.html e assets/app.js com
marcadores únicos) e aponta a env FRONTEND_DIST para ele via monkeypatch.
Cenários provados (truth do must_have do plano): fallback SPA (/ e /login
devolvem o MESMO index), assets como arquivos estáticos com Content-Type de
script, /api e /healthz intocados (o catch-all é registrado DEPOIS — a ordem
de registro é a precedência no roteador do Starlette), os TRÊS cabeçalhos de
segurança em todas as respostas (CSP sem unsafe-inline, nosniff, no-referrer)
e tolerância à ausência do dist (web sobe só-API). O escape de diretório
(T-05-09) nunca sai do dist: caminho que escapa cai no index.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from baixou.core.migrations import apply_migrations
from baixou.web.main import _dist_frontend, create_app
from fastapi.testclient import TestClient

_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
)

_INDEX = '<!doctype html><html lang="pt-BR"><body>INDEX-MARCADOR-05-02</body></html>'
_APP_JS = "console.log('ASSET-MARCADOR-05-02');"


@pytest.fixture()
def dist_falso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build Vite fingido: index.html + assets/app.js com conteúdo único."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(_INDEX, encoding="utf-8")
    (dist / "assets" / "app.js").write_text(_APP_JS, encoding="utf-8")
    monkeypatch.setenv("FRONTEND_DIST", str(dist))
    return dist


@pytest.fixture()
def cliente(settings, dist_falso) -> TestClient:
    """App com o dist falso montado e banco migrado (sem usuário)."""
    apply_migrations(settings.db_path)
    return TestClient(create_app(settings))


def _assert_cabecalhos(response) -> None:
    """A tríade de segurança da arquitetura, em TODA resposta do web."""
    assert response.headers["content-security-policy"] == _CSP
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_raiz_devolve_o_index_com_cabecalhos(cliente: TestClient) -> None:
    response = cliente.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "INDEX-MARCADOR-05-02" in response.text
    _assert_cabecalhos(response)


def test_fallback_spa_devolve_o_mesmo_index(cliente: TestClient) -> None:
    """Rota do Router sem arquivo no dist (/login) → o MESMO index.html."""
    raiz = cliente.get("/")
    login = cliente.get("/login")
    assert login.status_code == 200
    assert login.headers["content-type"].startswith("text/html")
    assert login.text == raiz.text
    _assert_cabecalhos(login)


def test_assets_sao_arquivos_estaticos(cliente: TestClient) -> None:
    response = cliente.get("/assets/app.js")
    assert response.status_code == 200
    assert "ASSET-MARCADOR-05-02" in response.text
    assert "javascript" in response.headers["content-type"]


def test_api_e_healthz_intocados_pelo_catch_all(cliente: TestClient) -> None:
    """Precedência de rotas: /api segue JSON e o /healthz segue contrato."""
    setup = cliente.get("/api/setup")
    assert setup.status_code == 200
    assert setup.json() == {"setup_pending": True}
    _assert_cabecalhos(setup)  # middleware é global: cabeçalhos também no /api

    # Banco recém-migrado não tem run.last_run → 503 do contrato (um catch-all
    # sombreando o /healthz devolveria o index 200 — este assertion reprova).
    assert cliente.get("/healthz").status_code == 503


def test_path_traversal_nunca_sai_do_dist(cliente: TestClient, tmp_path: Path) -> None:
    """T-05-09: arquivo FORA do dist nunca é servido pelo catch-all."""
    (tmp_path / "segredo.txt").write_text("FORA-DO-DIST", encoding="utf-8")
    for tentativa in ("/../segredo.txt", "/..%2Fsegredo.txt", "/segredo.txt"):
        response = cliente.get(tentativa)
        assert response.status_code == 200
        assert "FORA-DO-DIST" not in response.text
        assert "INDEX-MARCADOR-05-02" in response.text  # caiu no fallback SPA


def test_sem_dist_o_web_sobe_so_api(settings, tmp_path, monkeypatch) -> None:
    """Env apontando para diretório inexistente: sem mount, sem catch-all."""
    monkeypatch.setenv("FRONTEND_DIST", str(tmp_path / "dist-inexistente"))
    apply_migrations(settings.db_path)
    client = TestClient(create_app(settings))

    # Sem dist não há catch-all: GET / morre no 404 do roteador…
    assert client.get("/").status_code == 404
    # …e o /api segue normal (o web de pé só-API).
    setup = client.get("/api/setup")
    assert setup.status_code == 200
    assert setup.json() == {"setup_pending": True}
    _assert_cabecalhos(setup)


def test_env_front_dist_vazia_cai_no_padrao(monkeypatch, tmp_path) -> None:
    """WR-01: env definida como string vazia conta como AUSENTE (padrão do repo)."""
    monkeypatch.setenv("FRONTEND_DIST", "")
    caminho = _dist_frontend()
    assert caminho.name == "dist"
    assert caminho.parent.name == "frontend"
