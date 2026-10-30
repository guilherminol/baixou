"""Config fail-fast completa: _FILE, validação total e sigilo (OPS-02).

Casos do <behavior> do plano 01-02, Task 1: padrões, POLL_MINUTES, TZ_MARKET
(zona IANA, nunca offset), LOG_LEVEL, DATA_DIR não criável, mecanismo _FILE
(leitura, sigilo do valor, prioridade da variável sobre o arquivo) e tolerância
a variáveis desconhecidas. Toda captura de saída usa capsys — o valor de um
segredo jamais pode aparecer em log, mensagem de erro ou exceção (K2).
"""

from __future__ import annotations

import os

import pytest
from baixou.core.config import load_settings


def _base_env(monkeypatch, tmp_path) -> None:
    """Ambiente válido de base; cada teste sobrepõe só o que está testando."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ_MARKET", "America/Sao_Paulo")
    monkeypatch.setenv("POLL_MINUTES", "15")
    monkeypatch.setenv("LOG_LEVEL", "info")


def _assert_exit_names_variable(excinfo, variable: str, captured) -> None:
    """Falha de config: SystemExit, evento config_invalid, só o NOME aparece."""
    assert excinfo.type is SystemExit
    whole_output = captured.out + captured.err
    assert "config_invalid" in whole_output
    assert variable in whole_output


def test_defaults_without_env(monkeypatch, tmp_path):
    """Sem variáveis definidas (DATA_DIR isolado), valem os padrões da Fase 1."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("TZ_MARKET")
    monkeypatch.delenv("POLL_MINUTES")
    monkeypatch.delenv("LOG_LEVEL")

    settings = load_settings()

    assert settings.data_dir.name == "data"
    assert settings.tz_market == "America/Sao_Paulo"
    assert settings.poll_minutes == 15
    assert settings.log_level == "info"
    # Fase 2 (02-01): padrões das três variáveis novas (D-01; PRD: toda rodada)
    assert settings.brapi_token == ""
    assert settings.brapi_monthly_limit == 15000
    assert settings.b3_every_n_rounds == 1
    assert settings.secrets == {}


@pytest.mark.parametrize("variable", ["DATA_DIR", "TZ_MARKET", "POLL_MINUTES", "LOG_LEVEL"])
def test_empty_string_exits_naming_variable(monkeypatch, tmp_path, capsys, variable):
    """String vazia definida é inválida: impede a subida nomeando a variável (SC-3/WR-01).

    O padrão só vale quando a variável está de fato ausente — um valor presente,
    mesmo vazio, segue para a validação do campo e sai por config_invalid.
    """
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, "")

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    captured = capsys.readouterr()
    _assert_exit_names_variable(excinfo, variable, captured)
    if variable == "DATA_DIR":
        assert "caminho" in captured.out + captured.err  # hint PT-BR: não pode ser vazio


def test_poll_minutes_not_numeric_exits_naming_variable(monkeypatch, tmp_path, capsys):
    """POLL_MINUTES="abc" impede a subida; o valor nunca aparece na saída."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("POLL_MINUTES", "abc")

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    captured = capsys.readouterr()
    _assert_exit_names_variable(excinfo, "POLL_MINUTES", captured)
    assert "abc" not in captured.out + captured.err


@pytest.mark.parametrize("invalid", ["0", "-5"])
def test_poll_minutes_must_be_positive(monkeypatch, tmp_path, capsys, invalid):
    """POLL_MINUTES precisa ser inteiro > 0; a mensagem nomeia a variável."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("POLL_MINUTES", invalid)

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    _assert_exit_names_variable(excinfo, "POLL_MINUTES", capsys.readouterr())


def test_poll_minutes_valid_value_is_parsed(monkeypatch, tmp_path):
    """Valor válido vira int (ex.: "30" → 30)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("POLL_MINUTES", "30")
    assert load_settings().poll_minutes == 30


def test_tz_market_offset_refused_zone_names_accepted(monkeypatch, tmp_path, capsys):
    """TZ_MARKET aceita nome de zona IANA e recusa offset com texto explicativo."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TZ_MARKET", "UTC-03:00")

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    captured = capsys.readouterr()
    _assert_exit_names_variable(excinfo, "TZ_MARKET", captured)
    whole_output = captured.out + captured.err
    assert "zona" in whole_output  # texto PT-BR explica: use o nome da zona

    monkeypatch.setenv("TZ_MARKET", "Europe/Lisbon")
    assert load_settings().tz_market == "Europe/Lisbon"


def test_log_level_verbose_refused_debug_accepted(monkeypatch, tmp_path, capsys):
    """LOG_LEVEL fora do conjunto é recusado; debug é válido."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    _assert_exit_names_variable(excinfo, "LOG_LEVEL", capsys.readouterr())

    monkeypatch.setenv("LOG_LEVEL", "debug")
    assert load_settings().log_level == "debug"


def test_data_dir_unwritable_exits_naming_variable(monkeypatch, tmp_path, capsys):
    """DATA_DIR apontando para arquivo regular (não-criável como pasta) impede a subida."""
    blocker = tmp_path / "blocker"
    blocker.write_text("não sou uma pasta", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("DATA_DIR", str(blocker))

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    _assert_exit_names_variable(excinfo, "DATA_DIR", capsys.readouterr())


def test_secret_from_file_is_exposed_and_never_logged(monkeypatch, tmp_path, capsys):
    """SECRET_X_FILE é lido (newline final removida) e o valor nunca aparece na saída."""
    secret_file = tmp_path / "secret_x"
    secret_file.write_text("s3cr3t\n", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("SECRET_X", raising=False)
    monkeypatch.setenv("SECRET_X_FILE", str(secret_file))

    settings = load_settings()

    assert settings.secrets["SECRET_X"] == "s3cr3t"
    captured = capsys.readouterr()
    assert "s3cr3t" not in captured.out + captured.err


def test_missing_secret_file_exits_naming_file_variable(monkeypatch, tmp_path, capsys):
    """_FILE presente sem arquivo legível é erro nomeando a variável _FILE."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("SECRET_X", raising=False)
    monkeypatch.setenv("SECRET_X_FILE", str(tmp_path / "inexistente"))

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    _assert_exit_names_variable(excinfo, "SECRET_X_FILE", capsys.readouterr())


def test_env_variable_wins_over_file(monkeypatch, tmp_path, capsys):
    """Se NOME e NOME_FILE existem, NOME vence (arquitetura linha 190)."""
    secret_file = tmp_path / "secret_x"
    secret_file.write_text("do-arquivo\n", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("SECRET_X", "do-ambiente")
    monkeypatch.setenv("SECRET_X_FILE", str(secret_file))

    settings = load_settings()

    assert "SECRET_X" not in settings.secrets  # não foi lido via _FILE
    assert "do-arquivo" not in settings.secrets.values()
    captured = capsys.readouterr()
    assert "do-arquivo" not in captured.out + captured.err


def test_unknown_env_variables_are_tolerated(monkeypatch, tmp_path):
    """Variáveis desconhecidas não derrubam a subida (fases futuras acrescentam nomes)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("FUTURA_VARIAVEL", "qualquer")
    settings = load_settings()
    assert settings.poll_minutes == 15


def test_probe_cleanup_leaves_no_probe_files(monkeypatch, tmp_path):
    """Após a subida, nenhum arquivo de probe (prefixo .baixou-write-probe) resta no DATA_DIR."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setattr(os, "getpid", lambda: 4242)

    load_settings()

    assert list((tmp_path / "data").glob(".baixou-write-probe*")) == []


def test_probe_of_other_process_survives(monkeypatch, tmp_path):
    """A subida remove apenas o próprio probe — o legado de outro pid continua lá."""
    _base_env(monkeypatch, tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    legacy = data / ".baixou-write-probe.999999"
    legacy.write_text("outro-processo", encoding="utf-8")
    monkeypatch.setattr(os, "getpid", lambda: 4242)

    load_settings()

    assert legacy.exists()


# --- Fase 2 (plano 02-01, Task 2): BRAPI_TOKEN, BRAPI_MONTHLY_LIMIT, B3_EVERY_N_ROUNDS ---


@pytest.mark.parametrize("variable", ["BRAPI_TOKEN", "BRAPI_MONTHLY_LIMIT", "B3_EVERY_N_ROUNDS"])
def test_phase2_empty_string_exits_naming_variable(monkeypatch, tmp_path, capsys, variable):
    """String vazia definida é inválida — mesmo contrato WR-01 das vars da Fase 1."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, "")

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    captured = capsys.readouterr()
    _assert_exit_names_variable(excinfo, variable, captured)


def test_brapi_token_from_file_is_exposed_and_never_logged(monkeypatch, tmp_path, capsys):
    """BRAPI_TOKEN_FILE é lido, vira settings.brapi_token, entra em secrets e nunca ecoa (K2)."""
    secret_file = tmp_path / "brapi_token"
    secret_file.write_text("brapi-s3cr3t\n", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BRAPI_TOKEN_FILE", str(secret_file))

    settings = load_settings()

    assert settings.brapi_token == "brapi-s3cr3t"
    assert settings.secrets["BRAPI_TOKEN"] == "brapi-s3cr3t"
    captured = capsys.readouterr()
    assert "brapi-s3cr3t" not in captured.out + captured.err


def test_brapi_token_env_wins_over_file(monkeypatch, tmp_path, capsys):
    """Se BRAPI_TOKEN e BRAPI_TOKEN_FILE existem, a variável vence (arquitetura linha 190)."""
    secret_file = tmp_path / "brapi_token"
    secret_file.write_text("do-arquivo\n", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BRAPI_TOKEN", "do-ambiente")
    monkeypatch.setenv("BRAPI_TOKEN_FILE", str(secret_file))

    settings = load_settings()

    assert settings.brapi_token == "do-ambiente"
    assert "BRAPI_TOKEN" not in settings.secrets  # não foi lido via _FILE
    captured = capsys.readouterr()
    assert "do-arquivo" not in captured.out + captured.err


@pytest.mark.parametrize("invalid", ["abc", "0", "-5"])
def test_brapi_monthly_limit_invalid_exits_naming_variable(monkeypatch, tmp_path, capsys, invalid):
    """BRAPI_MONTHLY_LIMIT precisa ser inteiro > 0; a mensagem nomeia a variável."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BRAPI_MONTHLY_LIMIT", invalid)

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    _assert_exit_names_variable(excinfo, "BRAPI_MONTHLY_LIMIT", capsys.readouterr())


def test_brapi_monthly_limit_valid_value_is_parsed(monkeypatch, tmp_path):
    """Valor válido vira int (ex.: "20000" → 20000)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("BRAPI_MONTHLY_LIMIT", "20000")
    assert load_settings().brapi_monthly_limit == 20000


@pytest.mark.parametrize("invalid", ["0", "-1", "1.5", "abc"])
def test_b3_every_n_rounds_invalid_exits_naming_variable(monkeypatch, tmp_path, capsys, invalid):
    """B3_EVERY_N_ROUNDS precisa ser inteiro >= 1, com hint próprio (D-01)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("B3_EVERY_N_ROUNDS", invalid)

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    captured = capsys.readouterr()
    _assert_exit_names_variable(excinfo, "B3_EVERY_N_ROUNDS", captured)
    assert "use um inteiro maior ou igual a 1" in captured.out + captured.err


def test_b3_every_n_rounds_valid_value_is_parsed_and_keeps_poll_minutes(monkeypatch, tmp_path):
    """ "3" → 3; o intervalo das rodadas (e o TTL do lock /healthz) não muda (D-01)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("B3_EVERY_N_ROUNDS", "3")
    settings = load_settings()
    assert settings.b3_every_n_rounds == 3
    assert settings.poll_minutes == 15


# --- Fase 3 (plano 03-01, Task 1): limiares e canais no Settings (D-14, WR-01) ---


def test_phase3_defaults_without_env(monkeypatch, tmp_path):
    """Sem variáveis de canal/limiar definidas, valem os padrões da Fase 3 (D-14)."""
    _base_env(monkeypatch, tmp_path)
    for variable in (
        "REARM_PCT",
        "SUSPECT_PCT",
        "STALE_MINUTES_B3",
        "STALE_MINUTES_CRYPTO",
        "FAIL_NOTIFY_AFTER",
        "TELEGRAM_TOKEN",
        "TELEGRAM_CHAT_ID",
        "NTFY_URL",
        "NTFY_TOPIC",
        "NTFY_TOKEN",
        "NOTIFY_CLICK_URL",
    ):
        monkeypatch.delenv(variable, raising=False)

    settings = load_settings()

    assert settings.rearm_pct == 1
    assert settings.suspect_pct == 20
    assert settings.stale_minutes_b3 == 75
    assert settings.stale_minutes_crypto == 30
    assert settings.fail_notify_after == 3
    assert settings.ntfy_url == "https://ntfy.sh"
    assert settings.notify_click_url == ""
    assert settings.telegram_token == ""
    assert settings.telegram_chat_id == ""
    assert settings.ntfy_topic == ""
    assert settings.ntfy_token == ""


@pytest.mark.parametrize(
    ("variable", "value", "expected"),
    [
        ("REARM_PCT", "2", 2),
        ("SUSPECT_PCT", "15", 15),
        ("STALE_MINUTES_B3", "90", 90),
        ("STALE_MINUTES_CRYPTO", "45", 45),
        ("FAIL_NOTIFY_AFTER", "5", 5),
    ],
)
def test_phase3_int_valid_value_is_parsed(monkeypatch, tmp_path, variable, value, expected):
    """Valor válido vira int no molde das ints da Fase 2."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, value)
    settings = load_settings()
    assert getattr(settings, variable.lower()) == expected


@pytest.mark.parametrize(
    "variable",
    [
        "REARM_PCT",
        "SUSPECT_PCT",
        "STALE_MINUTES_B3",
        "STALE_MINUTES_CRYPTO",
        "FAIL_NOTIFY_AFTER",
    ],
)
@pytest.mark.parametrize("invalid", ["abc", "0", "-5", ""])
def test_phase3_int_invalid_exits_naming_variable(monkeypatch, tmp_path, capsys, variable, invalid):
    """Int da fase precisa ser inteiro > 0; string vazia definida também falha (WR-01)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, invalid)

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    _assert_exit_names_variable(excinfo, variable, capsys.readouterr())


def test_telegram_token_from_file_is_exposed_and_never_logged(monkeypatch, tmp_path, capsys):
    """TELEGRAM_TOKEN_FILE é lido, vira settings.telegram_token, entra em secrets e não ecoa."""
    secret_file = tmp_path / "telegram_token"
    secret_file.write_text("123456:bot-s3cr3t\n", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_TOKEN_FILE", str(secret_file))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")  # par completo (D-08)

    settings = load_settings()

    assert settings.telegram_token == "123456:bot-s3cr3t"
    assert settings.secrets["TELEGRAM_TOKEN"] == "123456:bot-s3cr3t"
    captured = capsys.readouterr()
    assert "bot-s3cr3t" not in captured.out + captured.err


def test_telegram_token_env_wins_over_file(monkeypatch, tmp_path, capsys):
    """Se TELEGRAM_TOKEN e TELEGRAM_TOKEN_FILE existem, a variável vence (molde BRAPI_TOKEN)."""
    secret_file = tmp_path / "telegram_token"
    secret_file.write_text("123456:do-arquivo\n", encoding="utf-8")
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_TOKEN", "123456:do-ambiente")
    monkeypatch.setenv("TELEGRAM_TOKEN_FILE", str(secret_file))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")  # par completo (D-08)

    settings = load_settings()

    assert settings.telegram_token == "123456:do-ambiente"
    assert "TELEGRAM_TOKEN" not in settings.secrets  # não foi lido via _FILE
    captured = capsys.readouterr()
    assert "do-arquivo" not in captured.out + captured.err


@pytest.mark.parametrize(
    "variable", ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "NTFY_TOPIC", "NTFY_TOKEN", "NTFY_URL"]
)
def test_phase3_channel_empty_string_exits_naming_variable(monkeypatch, tmp_path, capsys, variable):
    """String vazia definida em canal é inválida — mesmo contrato WR-01 (D-14)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, "")

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    captured = capsys.readouterr()
    _assert_exit_names_variable(excinfo, variable, captured)


def test_notify_click_url_empty_is_valid(monkeypatch, tmp_path):
    """NOTIFY_CLICK_URL vazio é VÁLIDO — padrão vazio, sem botão de abrir (D-14)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("NOTIFY_CLICK_URL", "")
    settings = load_settings()
    assert settings.notify_click_url == ""


# --- Fase 3 (plano 03-01, Task 3): pares e formato dos canais (D-08, D-09) ---


def test_telegram_token_without_chat_id_exits_naming_chat_id(monkeypatch, tmp_path, capsys):
    """TELEGRAM_TOKEN sem TELEGRAM_CHAT_ID: canal pela metade nomeia o que FALTA (D-08)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc-hash")

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    _assert_exit_names_variable(excinfo, "TELEGRAM_CHAT_ID", capsys.readouterr())


def test_telegram_chat_id_without_token_exits_naming_token(monkeypatch, tmp_path, capsys):
    """TELEGRAM_CHAT_ID sem TELEGRAM_TOKEN: o inverso também é canal pela metade (D-08)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    _assert_exit_names_variable(excinfo, "TELEGRAM_TOKEN", capsys.readouterr())


def test_ntfy_token_without_topic_exits_naming_topic(monkeypatch, tmp_path, capsys):
    """NTFY_TOKEN sem NTFY_TOPIC: nomeia o tópico que falta (D-08)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("NTFY_TOKEN", "tok-ntfy")

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    _assert_exit_names_variable(excinfo, "NTFY_TOPIC", capsys.readouterr())


def test_ntfy_topic_alone_is_valid(monkeypatch, tmp_path):
    """NTFY_TOPIC sem token é VÁLIDO — o token do ntfy é opcional (D-08)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("NTFY_TOPIC", "meu-topico")
    settings = load_settings()
    assert settings.ntfy_topic == "meu-topico"
    assert settings.ntfy_token == ""


@pytest.mark.parametrize(
    ("variable", "value", "pair", "secret"),
    [
        ("TELEGRAM_CHAT_ID", "abc", "123:abc-hash", "abc-hash"),
        ("TELEGRAM_TOKEN", "sem-dois-pontos", "42", "sem-dois-pontos"),
        ("NTFY_URL", "ftp://x", "", ""),
    ],
)
def test_invalid_format_exits_naming_variable_without_the_value(
    monkeypatch, tmp_path, capsys, variable, value, pair, secret
):
    """Formato inválido falha nomeando a variável; o valor nunca aparece (D-09/K2)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv(variable, value)
    if variable == "TELEGRAM_CHAT_ID":
        monkeypatch.setenv("TELEGRAM_TOKEN", pair)  # par completo: só o formato falha
    if variable == "TELEGRAM_TOKEN":
        monkeypatch.setenv("TELEGRAM_CHAT_ID", pair)

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    captured = capsys.readouterr()
    _assert_exit_names_variable(excinfo, variable, captured)
    if secret:  # valor vazio está em tudo; a asserção de não-vazamento vale para valores reais
        assert secret not in captured.out + captured.err


def test_negative_chat_id_is_accepted(monkeypatch, tmp_path):
    """chat_id de grupo é negativo e numérico — aceito (D-09)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TELEGRAM_TOKEN", "123:abc-hash")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-123")
    settings = load_settings()
    assert settings.telegram_chat_id == "-123"


def test_environment_without_any_channel_loads(monkeypatch, tmp_path):
    """Sem NENHUM canal configurado o worker sobe normal — avisos desligados (D-08)."""
    _base_env(monkeypatch, tmp_path)
    for variable in (
        "TELEGRAM_TOKEN",
        "TELEGRAM_CHAT_ID",
        "NTFY_TOPIC",
        "NTFY_TOKEN",
        "NTFY_URL",
    ):
        monkeypatch.delenv(variable, raising=False)

    settings = load_settings()

    assert settings.telegram_token == ""
    assert settings.telegram_chat_id == ""
    assert settings.ntfy_topic == ""
    assert settings.ntfy_token == ""
    assert settings.ntfy_url == "https://ntfy.sh"  # padrão público, sem dado do autor


# --- Fase 4 (plano 04-01, Task 2): COOKIE_SECURE (AUTH-01, molde WR-01/LOG_LEVEL) ---


def test_phase4_default_cookie_secure(monkeypatch, tmp_path):
    """Sem COOKIE_SECURE definida, o padrão é "auto" (Secure só em https)."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    assert load_settings().cookie_secure == "auto"


@pytest.mark.parametrize("valor", ["auto", "sim", "nao", "  SIM  ", "Nao"])
def test_cookie_secure_valid_value_is_normalized(monkeypatch, tmp_path, valor):
    """Valor válido aparado em minúsculo — conjunto fechado auto/sim/nao."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COOKIE_SECURE", valor)
    assert load_settings().cookie_secure == valor.strip().lower()


@pytest.mark.parametrize("invalid", ["abc", "always", ""])
def test_cookie_secure_invalid_exits_naming_variable(monkeypatch, tmp_path, capsys, invalid):
    """Fora do conjunto (ou string vazia DEFINIDA) impede a subida nomeando a variável."""
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COOKIE_SECURE", invalid)

    with pytest.raises(SystemExit) as excinfo:
        load_settings()
    captured = capsys.readouterr()
    _assert_exit_names_variable(excinfo, "COOKIE_SECURE", captured)
    if invalid:  # o valor nunca aparece na mensagem (K2/WR-01)
        assert invalid not in captured.out + captured.err
