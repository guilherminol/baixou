"""POST /api/notifiers/{canal}/test — aviso real, resultado síncrono, zero gravação (D-05).

O botão de teste da tela /status (NOTIF-02, FR-18) dispara um envio REAL pelo
módulo do canal (o mesmo ``send`` do worker — a regra do resultado tipado
D-16 substitui a exceção) e devolve o resultado NA HORA: falha de entrega do
canal é RESULTADO do teste ({ok: false, mensagem PT-BR citando kind e código
HTTP}), nunca erro HTTP — todo caminho tentado responde 200 (D-05). Canal sem
configuração → 409 ANTES de qualquer envio; canal fora do Literal → 422 no
envelope D-08 pelo handler global. O endpoint NÃO TEM estado compartilhado
mutável e NÃO toca o banco em nenhum caminho (nem concorrente): nenhuma
conexão é aberta, nada é persistido — o único efeito é o aviso na rede
externa (T-05-04: exige sessão e é ação explícita do admin único).

K2/T-05-01: as mensagens citam apenas kind e código HTTP — os módulos de canal
já saneiam URL/token/tópico (T-03-01/T-03-02) e nada disso atravessa a
resposta. ``_cliente`` é emenda de teste (os testes substituem por
MockTransport — molde _cliente_validacao de watch.py).
"""

from __future__ import annotations

from typing import Literal

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from baixou.web.auth import Sessao
from baixou.web.schemas import TesteNotificador

router = APIRouter()

_TITULO_TESTE = "Baixou — aviso de teste"
_CORPO_TESTE = "Aviso de teste disparado pela interface web."
_TIMEOUT_S = 10.0  # timeout curto do molde de validação (symbol_validation)


def _cliente() -> httpx.Client:
    """Cliente httpx de vida curta com timeout — fechado no finally pelo handler."""
    return httpx.Client(timeout=_TIMEOUT_S)


@router.post("/api/notifiers/{canal}/test", response_model=TesteNotificador)
def testar_canal(
    request: Request, canal: Literal["telegram", "ntfy"], usuario: Sessao
) -> TesteNotificador | JSONResponse:
    """Envia UM aviso de teste pelo canal e devolve {ok, mensagem} (D-05).

    Import dos módulos de canal DENTRO do handler (molde configured_channels);
    409 para canal não configurado vem ANTES de criar cliente ou rede. O
    resultado tipado do canal é mapeado SEM levantar: aceite → ok; recusa com
    código HTTP → kind + HTTP; falha de transporte (sem código) → só o kind.
    Nenhuma escrita em lugar nenhum: o módulo nem importa o helper de conexão.
    """
    del usuario
    settings = request.app.state.settings
    from baixou.worker.notifiers import ntfy, telegram

    modulo = telegram if canal == "telegram" else ntfy
    if not modulo.is_configured(settings):
        return JSONResponse(status_code=409, content={"detail": "canal não configurado"})
    cliente = _cliente()
    try:
        resultado = modulo.send(cliente, settings, _TITULO_TESTE, _CORPO_TESTE)
    finally:
        cliente.close()
    if resultado.ok:
        return TesteNotificador(ok=True, mensagem="aviso de teste aceito pelo canal")
    if resultado.http_status is not None:
        mensagem = (
            f"o canal recusou o aviso de teste "
            f"(erro {resultado.kind}, HTTP {resultado.http_status})"
        )
    else:
        mensagem = f"o canal não respondeu ao teste (erro {resultado.kind})"
    return TesteNotificador(ok=False, mensagem=mensagem)
