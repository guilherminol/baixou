"""Montagem das mensagens de aviso: título estável e corpos PT-BR (D-01, D-03, D-04, D-07).

O título é obrigatório e estável (arquitetura §Interfaces de fontes e
notificadores) — "Baixou: <SÍMBOLO>" (D-03): o nome do projeto é o próprio
evento; os números ficam no corpo. O corpo do aviso de preço é completo
(D-01): preço atual, alvo, variação do dia e hora da cotação convertida para
o fuso do pregão, com a moeda derivada do market/sufixo (D-02 — vive no
núcleo comum ``baixou.core.currency`` desde o 04-02; reexportada aqui para
os imports da Fase 3 seguirem intactos) e a nota de conferência quando a
variação é suspeita (D-04/D-10) como linha extra, sem bloquear o alerta. Os
construtores de avisos operacionais (falha/recuperação de fonte, cota
80%/esgotada — D-07, consumidos pelo plano 03-04) compartilham o formato
título+corpo e NUNCA levam nota de suspeita. A mensagem fica restrita a
dados do item — nunca linguagem de compra/venda (o aviso não é
recomendação) — e nunca inclui URL, token, tópico ou chat_id (K2/T-03-02).
Horários ficam em UTC no banco; conversão para fuso só aqui, na saída.
A formatação de apresentação (format_money/format_pct) fica no worker —
só a moeda é compartilhada. Helpers puros, sem I/O.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from baixou.core.config import Settings
from baixou.core.currency import currency_for  # reexport (WATCH-02, D-02)

# Simultânea: vírgula vira ponto e ponto vira vírgula (67,320.00 → 67.320,00).
_PT_BR_DIGITS = str.maketrans(",.", ".,")


def alert_title(symbol: str) -> str:
    """Título estável do aviso de preço (D-03) — idêntico em todos os canais."""
    return f"Baixou: {symbol}"


def format_money(value: float, currency: str) -> str:
    """Dinheiro em PT-BR (D-02): 2 casas, milhar "." e decimal ","."""
    return f"{currency} {f'{value:,.2f}'.translate(_PT_BR_DIGITS)}"


def format_pct(change_pct: float) -> str:
    """Variação com sinal explícito e vírgula decimal (D-01): "-1,2%", "+20%"."""
    formatted = f"{change_pct:+.2f}".translate(_PT_BR_DIGITS).rstrip("0").rstrip(",")
    return f"{formatted}%"


def suspect_note(change_pct: float, suspect_pct: float) -> str:
    """Nota de conferência da variação suspeita (D-04) — string vazia quando normal.

    A borda é INCLUSIVA em módulo (D-10): abs(change_pct) ≥ suspect_pct em
    qualquer direção — queda brusca também pede conferência.
    """
    if abs(change_pct) >= suspect_pct:
        return (
            f"variação de {format_pct(change_pct)} hoje — confira se houve "
            "evento corporativo (desdobramento, bonificação)"
        )
    return ""


def _hora_local(price_at: str, tz_name: str) -> str:
    """Hora da cotação no fuso do pregão; timestamp ilegível aparece cru.

    Ilegível nunca é esperado (o gravador é nosso) — aparece cru em vez de
    derrubar a rodada: formatação de mensagem nunca derruba nada.
    """
    try:
        quoted_at = datetime.fromisoformat(price_at.replace("Z", "+00:00"))
        return quoted_at.astimezone(ZoneInfo(tz_name)).strftime("%d/%m/%Y %H:%M")
    except ValueError, ZoneInfoNotFoundError:
        return price_at


def price_alert_body(
    symbol: str,
    market: str,
    price: float,
    target_below: float,
    change_pct: float | None,
    price_at: str,
    settings: Settings,
) -> str:
    """Corpo completo do aviso de preço (D-01/D-02/D-04).

    Preço atual e alvo com a moeda de ``currency_for``, variação do dia
    (linha omitida quando a fonte não a fornece), hora da cotação no fuso
    ``settings.tz_market`` e a nota de suspeita como linha extra quando
    houver — a nota nunca bloqueia o alerta (D-04).
    """
    currency = currency_for(symbol, market)
    lines = [
        f"Preço atual: {format_money(price, currency)}",
        f"Alvo: {format_money(target_below, currency)}",
    ]
    if change_pct is not None:
        lines.append(f"Variação hoje: {format_pct(change_pct)}")
    hora = _hora_local(price_at, settings.tz_market)
    lines.append(f"Cotação de: {hora} ({settings.tz_market})")
    if change_pct is not None:
        note = suspect_note(change_pct, settings.suspect_pct)
        if note:
            lines.append(note)
    return "\n".join(lines)


def source_failure_message(source: str, kind: str, consecutive: int) -> tuple[str, str]:
    """Aviso operacional de falha prolongada de fonte (D-05/D-07): título + corpo."""
    title = f"Baixou: fonte {source} com falha"
    body = f"A fonte {source} falha há {consecutive} rodadas seguidas (motivo: {kind})."
    return title, body


def source_recovery_message(source: str) -> tuple[str, str]:
    """Aviso operacional de volta ao normal da fonte (D-05/D-07): título + corpo."""
    title = f"Baixou: fonte {source} normalizada"
    body = f"A fonte {source} voltou a responder normalmente."
    return title, body


def quota_80_message(calls: int, limit: int) -> tuple[str, str]:
    """Aviso operacional dos 80% da cota mensal da brapi (D-05/D-07): título + corpo."""
    title = "Baixou: 80% da cota da brapi"
    body = f"80% da cota mensal da brapi consumida: {calls} de {limit} chamadas."
    return title, body


def quota_exhausted_message() -> tuple[str, str]:
    """Aviso operacional da cota esgotada — B3 em pausa até virar o mês (D-05/D-07)."""
    title = "Baixou: cota da brapi esgotada"
    body = (
        "A cota mensal da brapi acabou — a consulta das ações da B3 fica em "
        "pausa até a virada do mês."
    )
    return title, body


def backup_failure_message(consecutive: int) -> tuple[str, str]:
    """Aviso operacional do backup noturno falhando (D-08): título + corpo."""
    title = "Baixou: backup noturno com falha"
    body = f"O backup noturno do banco falha há {consecutive} noites seguidas."
    return title, body


def backup_recovery_message() -> tuple[str, str]:
    """Aviso operacional de volta ao normal do backup noturno (D-08): título + corpo."""
    title = "Baixou: backup noturno normalizado"
    body = "O backup noturno voltou a funcionar — o banco foi copiado com sucesso."
    return title, body
