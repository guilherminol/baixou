"""Fronteira de importação worker/core × fastapi/pydantic (D-07, ROADMAP critério 4).

Dois testes via subprocesso (nunca import in-process: o sys.modules do pytest
já vem poluído) e sem rede:

- **Fronteira:** importar ``baixou.worker.main`` e ``baixou.core.config`` num
  processo limpo só sai 0 se fastapi e pydantic NÃO estiverem em sys.modules.
- **Caso-controle (prova de fail-first):** importar ``baixou.web.main`` só sai
  0 se o framework ESTIVER em sys.modules — se este controle algum dia falhar,
  o teste de fronteira está cego (a asserção não tem dentes).

Os nomes proibidos (framework web e a biblioteca de schemas dele) estão
nomeados literalmente aqui, dentro do próprio teste — nunca no código de
produção. A aplicação pela análise estática (ruff TID251) vive no
``pyproject.toml`` e vira gate de CI no plano 01-03.
"""

from __future__ import annotations

import subprocess
import sys

_TIMEOUT_S = 120

_WORKER_PROBE = """\
import sys

import baixou.core.config
import baixou.worker.main

prohibited = [name for name in ("fastapi", "pydantic") if name in sys.modules]
if prohibited:
    sys.exit(f"fronteira violada: {prohibited} em sys.modules do worker/core")
print("boundary-ok")
"""

_WEB_CONTROL_PROBE = """\
import sys

import baixou.web.main

present = [name for name in ("fastapi", "pydantic") if name in sys.modules]
if not present:
    sys.exit("controle cego: fastapi deveria estar em sys.modules após importar baixou.web.main")
print("control-ok")
"""


def _run_probe(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
    )


def test_worker_import_leaves_no_web_framework_in_sys_modules():
    """Worker/core não deixam fastapi nem pydantic em sys.modules (D-07)."""
    result = _run_probe(_WORKER_PROBE)
    assert result.returncode == 0, result.stderr


def test_web_control_proves_detection_has_teeth():
    """Controle positivo: a asserção detecta o framework quando ele É importado."""
    result = _run_probe(_WEB_CONTROL_PROBE)
    assert result.returncode == 0, result.stderr
