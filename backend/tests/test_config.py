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
