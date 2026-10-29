"""Contrato OpenAPI congelado — o objetivo declarado da fase (plano 04-04).

``backend/openapi.json`` é o artefato que a Fase 5 consome para gerar os tipos
TypeScript (openapi-typescript): o teste regenera ``app.openapi()`` em memória
e compara com o arquivo commitado em IGUALDADE ESTRUTURAL (JSON normalizado
com ``sort_keys``) — QUALQUER diferença reprova, o mesmo cheque que o CI
imporá ao frontend na Fase 5. Mudança de rota intencional? Regere o artefato
e commita junto (a mensagem do assert ensina como). O conjunto de paths é
EXATO — nem mais (``/healthz`` fora, ``include_in_schema=False``), nem menos
(todas as rotas /api da fase) — e o schema expõe os nomes dos modelos de
contrato. A documentação automática do FastAPI permanece DESLIGADA: o JSON é
artefato de build/CI, não rota servida.
"""

from __future__ import annotations

import json
from pathlib import Path

from baixou.core.config import Settings
from baixou.web.main import create_app
from fastapi.testclient import TestClient

_ARTEFATO = Path(__file__).resolve().parents[1] / "openapi.json"

# Toda a superfície /api da fase — o conjunto EXATO dos paths e métodos.
_PATHS_ESPERADOS = {
    "/api/setup": {"get", "post"},
    "/api/auth/login": {"post"},
    "/api/auth/logout": {"post"},
    "/api/watch": {"get", "post", "put"},
    "/api/watch/{symbol}/{market}": {"patch", "delete"},
}

# Os modelos de contrato (response_model e corpos) dos quais a Fase 5 deriva
# os tipos TypeScript.
_MODELOS_ESPERADOS = (
    "SetupRequest",
    "LoginRequest",
    "WatchItem",
    "WatchListResponse",
    "PatchWatch",
    "PutWatch",
)

_METHOD_KEYS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


def _settings_de_teste(tmp_path) -> Settings:
    """Settings mínima de teste: data_dir volátil; resto nos padrões do dataclass.

    O schema NÃO depende de configuração (nenhum valor de Settings entra no
    contrato — nenhum segredo no artefato); os 4 campos obrigatórios recebem
    valores determinísticos só para construir o app.
    """
    return Settings(
        data_dir=tmp_path,
        tz_market="America/Sao_Paulo",
        poll_minutes=15,
        log_level="info",
    )


def _schema_do_app(tmp_path) -> dict:
    """``app.openapi()`` com Settings mínima de teste (data_dir volátil)."""
    return create_app(_settings_de_teste(tmp_path)).openapi()


def _congelado() -> dict:
    assert _ARTEFATO.exists(), (
        "backend/openapi.json ausente — gere o artefato com "
        "json.dumps(app.openapi(), sort_keys=True, indent=2, ensure_ascii=False) "
        "+ newline a partir de create_app(Settings) e commita"
    )
    return json.loads(_ARTEFATO.read_text(encoding="utf-8"))


def test_contrato_congelado_igualdade_estrutural_com_o_app(tmp_path):
    """Drift reprova: o JSON commitado é IGUAL ao app.openapi() regenerado."""
    schema = _schema_do_app(tmp_path)
    congelado = _congelado()
    assert json.dumps(congelado, sort_keys=True) == json.dumps(schema, sort_keys=True), (
        "DRIFT entre app.openapi() e backend/openapi.json — se a mudança de rota é "
        "INTENCIONAL, regere o artefato (json.dumps(app.openapi(), sort_keys=True, "
        "indent=2, ensure_ascii=False) + newline em backend/openapi.json) e commita "
        "junto; se não é, reverta a mudança de rota"
    )


def test_conjunto_exato_de_paths_e_metodos():
    """Nem mais (healthz fora do contrato), nem menos (todas as rotas da fase)."""
    paths = _congelado()["paths"]
    assert set(paths) == set(_PATHS_ESPERADOS), (
        f"paths do contrato {sorted(paths)} != esperado {sorted(_PATHS_ESPERADOS)}"
    )
    assert "/healthz" not in paths
    for caminho, metodos in _PATHS_ESPERADOS.items():
        presentes = set(paths[caminho]) & _METHOD_KEYS
        assert presentes == metodos, f"{caminho}: {sorted(presentes)} != {sorted(metodos)}"


def test_schema_expoe_os_modelos_de_contrato():
    """A Fase 5 deriva os tipos dos componentes nomeados (WatchItem etc.)."""
    schemas = _congelado()["components"]["schemas"]
    ausentes = [nome for nome in _MODELOS_ESPERADOS if nome not in schemas]
    assert ausentes == [], f"modelos de contrato ausentes do schema: {ausentes}"


def test_documentacao_automatica_permanece_desligada(tmp_path):
    """Contrato da arquitetura: docs/redoc/openapi_url None — /docs responde 404."""
    app = create_app(_settings_de_teste(tmp_path))
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    with TestClient(app) as client:
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
