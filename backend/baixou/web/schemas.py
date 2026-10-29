"""Schemas pydantic da API de autenticação e da lista + o envelope de erro D-08.

O envelope de erro é contrato CONGELADO desta fase (D-08, reversibility
costly): ``{"erros": [{"symbol", "campo", "problema"}]}`` em PT-BR, um item
por erro — a Fase 5 renderiza cada entrada direto no formulário.
``symbol`` é None fora de validação de ticker. ``WatchItem`` também é
contrato (WATCH-01/WATCH-02): serve de corpo do POST e do item do lote do
PUT (os campos de cotação têm default e são ignorados na entrada) e de item
da resposta do GET — a moeda sai da função única ``currency_for``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class SetupRequest(BaseModel):
    """Corpo do POST /api/setup (SC-1): token do log + credenciais do admin."""

    token: str
    username: str
    password: str


class LoginRequest(BaseModel):
    """Corpo do POST /api/auth/login; "lembrar de mim" default desligado (D-10)."""

    username: str
    password: str
    remember: bool = False


class WatchItem(BaseModel):
    """Item da lista com a cotação e a moeda — corpo do POST e item do PUT (D-06).

    Na ENTRADA só symbol/market/target_below interessam (o resto tem default
    e campos extras são ignorados); na SAÍDA (GET/POST/PATCH/PUT) todos os
    campos vêm preenchidos — ``currency`` via ``currency_for``, campos de
    quote NULL quando não há cotação e ``notify_state`` como objeto JSON
    parseado ou null (nunca string crua). ``target_below`` exige > 0 (SC-4).
    ``paused`` é exposto desde o 04-03 (WATCH-03): o cadastro nasce ATIVO —
    a pausa é exclusivo do PATCH e o item pausado segue VISÍVEL na lista.

    ``fresh``/``suspect`` são contrato CONGELADO da Fase 5 (D-02, reversibility
    costly): estado derivado NO SERVIDOR — o SERVIDOR conhece os limiares
    (STALE_MINUTES_*, TZ_MARKET/pregão, SUSPECT_PCT), nunca o navegador, que
    recebe só o estado pronto para a coluna "Estado" da página de alertas.
    Campos de SAÍDA com default (mesmo padrão do ``paused`` do 04-03):
    ignorados na entrada. ``fresh`` usa os MESMOS quatro estados de
    ``freshness_state`` (worker/alerts: none → closed → stale → ok) e
    ``suspect`` a MESMA borda inclusiva da nota de suspeita do aviso
    (abs(change_pct) ≥ SUSPECT_PCT) — a página mostra o que o worker avalia.
    """

    symbol: str
    market: Literal["b3", "crypto"]
    # allow_inf_nan=False (WR-04): JSON 1e999 vira +Infinity e pydantic aceitava
    # — item com alvo inf que o GET devolvia como null e o worker nunca alertava.
    target_below: float = Field(gt=0, allow_inf_nan=False)
    paused: bool = False
    currency: str = ""
    price: float | None = None
    price_at: str | None = None
    change_pct: float | None = None
    alerted: bool = False
    alerted_at: str | None = None
    notify_state: dict[str, Any] | None = None
    fresh: Literal["ok", "stale", "closed", "none"] = "none"
    suspect: bool = False


class WatchListResponse(BaseModel):
    """Envelope do GET/PUT /api/watch: a lista completa, ordenada (market, symbol)."""

    itens: list[WatchItem]


class PatchWatch(BaseModel):
    """Corpo do PATCH /api/watch/{symbol}/{market}: alvo e/ou pausa (WATCH-03).

    Campos INDEPENDENTES e opcionais: SÓ os presentes no corpo mudam (o
    handler aplica por ``COALESCE``). Ao menos um campo é OBRIGATÓRIO — o
    modelo vazio é recusado pelo validador (422 no envelope D-08) e
    ``target_below`` mantém o gt=0 (SC-4). ``paused`` aceita só booleano.
    """

    # Mesma finitude do WatchItem (WR-04): PATCH também recusa +Infinity.
    target_below: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    paused: bool | None = None

    @model_validator(mode="after")
    def _exige_ao_menos_um_campo(self) -> PatchWatch:
        if self.target_below is None and self.paused is None:
            raise ValueError("informe ao menos um campo para atualizar")
        return self


class PutWatch(BaseModel):
    """Corpo do PUT /api/watch em lote (D-06): cria/edita numa transação única.

    ``min_length=1``: PUT com corpo vazio é erro de FORMA (422). O lote NUNCA
    remove (D-07): itens ausentes do corpo permanecem — remoção existe só
    pelo DELETE explícito.
    """

    itens: list[WatchItem] = Field(min_length=1)


class StatusCota(BaseModel):
    """Cota mensal da brapi exposta ao admin — contrato CONGELADO (D-03, Fase 5).

    ``usadas`` é derivado da leitura: chamadas do MÊS UTC corrente (linha de
    mês anterior → usadas 0, molde b3_quota_allows — a virada é derivada na
    leitura, sem migração); ``limite`` vem de Settings. Nenhum limiar de aviso
    atravessa o corpo (proibição D-02).
    """

    usadas: int
    limite: int


class StatusFonte(BaseModel):
    """Estado de UMA fonte (brapi/binance) derivado NO SERVIDOR — contrato CONGELADO (D-03).

    ``estado`` usa o vocabulário PT fechado da tela /status: "operacional"
    (consecutive_failures == 0), "erro" (0 < n < FAIL_NOTIFY_AFTER) e
    "falha_prolongada" (n >= FAIL_NOTIFY_AFTER, borda INCLUSIVA) — o limiar
    fica no servidor, o navegador recebe só o estado pronto (proibição D-02).
    Erro aparece com o nome da fonte (literal UI-02). Campo novo não pode
    nascer aqui sem re-freeze do openapi.json.
    """

    nome: str
    estado: str
    falhas_seguidas: int
    ultimo_sucesso: str | None = None
    ultimo_erro: str | None = None


class StatusCanal(BaseModel):
    """Canal de aviso (telegram/ntfy) para a tela /status — contrato CONGELADO (D-03/D-15).

    ``configurado`` deriva do Settings do web (is_configured de cada módulo de
    canal); ``ultimo_resultado`` é DERIVADO do notify_state do item mais
    recentemente alertado (formato congelado D-15 da Fase 3, sem migração) —
    valores "ok" ou "erro <kind> (HTTP <n>)". Só {nome, configurado} toca
    configuração: NENHUM valor de TELEGRAM_TOKEN/NTFY_TOPIC/chat_id aparece
    no corpo (K2/T-05-01).
    """

    nome: str
    configurado: bool
    ultimo_resultado: str | None = None


class StatusResponse(BaseModel):
    """Envelope do GET /api/status — contrato CONGELADO (D-03, reversibility costly).

    UM endpoint alimenta o cabeçalho da página de alertas E a tela /status
    inteira (UI-02); itens NÃO entram (já vêm do /api/watch). ``ultima_rodada``
    é a string ISO de run.last_run (parse tolerante, null quando ausente ou
    ilegível); ``proxima_rodada`` é DERIVADA no servidor com ``next_slot`` do
    núcleo comum (D-04) — nunca lida de tabela que pode defasar. Os nomes PT
    sem acento são DEFINITIVOS: geram os tipos TypeScript da Fase 5.
    """

    ultima_rodada: str | None = None
    proxima_rodada: str
    cota: StatusCota
    fontes: list[StatusFonte]
    canais: list[StatusCanal]


def erro_item(symbol: str | None, campo: str, problema: str) -> dict:
    """Monta UMA entrada do envelope D-08 (PT-BR em ``problema``)."""
    return {"symbol": symbol, "campo": campo, "problema": problema}
