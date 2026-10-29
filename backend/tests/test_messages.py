"""Conteúdo das mensagens: moeda, formatação PT-BR, suspeita e operacionais (D-01..D-04, D-07).

Helpers puros sem rede (molde de sources/base.py): moeda derivada de
market/sufixo, dinheiro com milhar "." e decimal ",", variação com sinal
explícito e vírgula, borda INCLUSIVA da suspeita nos dois sentidos (D-10),
corpo completo com preço/alvo/variação/hora no fuso TZ_MARKET e
construtores operacionais com título "Baixou: <evento>" e NENHUMA nota de
suspeita (D-04/D-07) — nenhum texto carrega URL, token, tópico ou chat_id.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from baixou.core.config import Settings
from baixou.worker import messages


def _settings(**overrides) -> Settings:
    values = {
        "data_dir": Path("."),  # irrelevante: messages não toca disco nem rede
        "tz_market": "America/Sao_Paulo",
        "poll_minutes": 15,
        "log_level": "info",
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("symbol", "market", "expected"),
    [
        ("PETR4", "b3", "R$"),
        ("BTCUSDT", "crypto", "US$"),
        ("ETHBRL", "crypto", "R$"),
        ("SOLUSD", "crypto", "US$"),
        ("DOGE27", "crypto", "US$"),  # sufixo exótico → padrão do mercado cripto
    ],
)
def test_currency_for_derives_from_market_and_suffix(symbol, market, expected):
    assert messages.currency_for(symbol, market) == expected


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [(15.2, "R$", "R$ 15,20"), (67320.0, "US$", "US$ 67.320,00")],
)
def test_format_money_renders_pt_br(value, currency, expected):
    assert messages.format_money(value, currency) == expected


@pytest.mark.parametrize(
    ("change_pct", "expected"),
    [(-1.2, "-1,2%"), (20.0, "+20%"), (-6.0, "-6%"), (1.25, "+1,25%")],
)
def test_format_pct_explicit_sign_and_comma(change_pct, expected):
    assert messages.format_pct(change_pct) == expected


def test_price_alert_body_full_body_with_currency_variation_and_local_hour():
    body = messages.price_alert_body(
        "PETR4", "b3", 15.2, 18.0, -1.2, "2026-10-28T16:00:00Z", _settings()
    )
    assert "Preço atual: R$ 15,20" in body
    assert "Alvo: R$ 18,00" in body
    assert "Variação hoje: -1,2%" in body
    # 16:00 UTC → 13:00 em Brasília (fuso TZ_MARKET — D-01)
    assert "Cotação de: 28/10/2026 13:00 (America/Sao_Paulo)" in body


def test_price_alert_body_crypto_uses_dollar():
    body = messages.price_alert_body(
        "BTCUSDT", "crypto", 67320.0, 70000.0, -1.2, "2026-10-28T16:00:00Z", _settings()
    )
    assert "Preço atual: US$ 67.320,00" in body


@pytest.mark.parametrize("change_pct", [20.0, -20.0])
def test_suspect_note_inclusive_border_in_both_directions(change_pct):
    note = messages.suspect_note(change_pct, 20)  # borda exata (D-10)
    assert "evento corporativo" in note
    assert "variação de" in note


@pytest.mark.parametrize("change_pct", [19.9, -19.9])
def test_suspect_note_absent_below_border(change_pct):
    assert messages.suspect_note(change_pct, 20) == ""


def test_price_alert_body_carries_suspect_note_as_extra_line_at_border():
    body = messages.price_alert_body(
        "PETR4", "b3", 15.2, 18.0, -20.0, "2026-10-28T16:00:00Z", _settings()
    )
    lines = body.split("\n")
    assert "variação de -20% hoje" in body  # nota de conferência (D-04)
    assert "Variação hoje: -20%" in lines[2]  # sem bloquear o aviso (D-04)
    assert "evento corporativo" in lines[-1]  # linha EXTRA, no fim do corpo


def test_price_alert_body_without_change_pct_omits_line_and_note():
    body = messages.price_alert_body(
        "PETR4", "b3", 15.2, 18.0, None, "2026-10-28T16:00:00Z", _settings()
    )
    assert "Variação hoje" not in body  # linha omitida quando a fonte não fornece
    assert "evento corporativo" not in body
    assert "Preço atual: R$ 15,20" in body  # o resto do corpo segue completo


def test_operational_constructors_have_baixou_title_and_no_suspect_note():
    constructors = [
        messages.source_failure_message("brapi", "rate_limit", 3),
        messages.source_recovery_message("brapi"),
        messages.quota_80_message(12000, 15000),
        messages.quota_exhausted_message(),
    ]
    for title, body in constructors:
        assert title.startswith("Baixou: ")  # D-07: título "Baixou: <evento>"
        assert body  # corpo PT-BR presente
        assert "evento corporativo" not in body  # nota nunca em operacionais (D-04)


def test_source_failure_message_cites_consecutive_rounds_and_kind():
    title, body = messages.source_failure_message("brapi", "rate_limit", 3)
    assert title == "Baixou: fonte brapi com falha"
    assert "3" in body and "rate_limit" in body  # rodadas seguidas + kind


def test_quota_exhausted_message_cites_b3_pause_until_month_turn():
    title, body = messages.quota_exhausted_message()
    assert title == "Baixou: cota da brapi esgotada"
    assert "B3" in body and "mês" in body  # pausa da B3 até virar o mês


def test_no_message_carries_url_token_topic_or_chat_id():
    produced = [
        messages.alert_title("PETR4"),
        messages.price_alert_body(
            "PETR4", "b3", 15.2, 18.0, -1.2, "2026-10-28T16:00:00Z", _settings()
        ),
        messages.source_failure_message("brapi", "rate_limit", 3),
        messages.source_recovery_message("brapi"),
        messages.quota_80_message(12000, 15000),
        messages.quota_exhausted_message(),
    ]
    for text in produced:
        assert "http" not in text
        assert "token" not in text
        assert "topic" not in text
        assert "chat_id" not in text
