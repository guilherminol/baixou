"""Configuração por variáveis de ambiente (OPS-02).

Stdlib puro — este módulo é importado pelo worker (D-07): proibido dotenv e
pydantic. Uma variável inválida impede a subida nomeando a variável; o valor
lido nunca aparece na mensagem nem no log (K2). O padrão de cada variável só
se aplica quando ela está de fato ausente do ambiente — um valor presente,
mesmo vazio, segue para a validação. Segredos aceitam o sufixo
``_FILE`` (convenção dos secrets do Docker): ``NOME_FILE`` lê o valor de um
arquivo; a variável ``NOME`` direta tem prioridade sobre o arquivo.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from baixou.core.log import log

_VALID_LOG_LEVELS = ("debug", "info", "warning", "error")
_FILE_SUFFIX = "_FILE"


@dataclass(frozen=True)
class Settings:
    """Configuração tipada da Fase 1."""

    data_dir: Path
    tz_market: str
    poll_minutes: int
    log_level: str
    # Fase 2 (POLL-01/POLL-02, D-01): os campos nascem com o contrato final;
    # a resolução NOME → NOME_FILE → padrão em load_settings é ligada na
    # Task 2 do plano 02-01 — até lá os defaults abaixo já são os padrões.
    brapi_token: str = ""
    brapi_monthly_limit: int = 15000
    b3_every_n_rounds: int = 1
    # Fase 3 (D-14): limiares da máquina de estados e canais de aviso — mesmo
    # contrato WR-01 da Fase 2: o padrão só vale na ausência real; string vazia
    # DEFINIDA é config_invalid nomeando a variável. Valores nunca aparecem em
    # log/erro (K2); a validação cruzada de pares e o formato leve ficam depois
    # da resolução individual (D-08/D-09).
    rearm_pct: int = 1
    suspect_pct: int = 20
    stale_minutes_b3: int = 75
    stale_minutes_crypto: int = 30
    fail_notify_after: int = 3
    telegram_token: str = ""
    telegram_chat_id: str = ""
    ntfy_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""
    notify_click_url: str = ""  # única env da fase onde vazio é válido (D-14)
    # Pares lidos via _FILE, separados dos campos comuns (fases 2–3 consomem
    # TELEGRAM_TOKEN etc. por aqui). Valores aqui NUNCA vão para log/erro (K2).
    secrets: dict[str, str] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        """Caminho do banco SQLite único (WAL)."""
        return self.data_dir / "baixou.db"


def _fail(variable: str, hint: str = "") -> NoReturn:
    """Registra config_invalid e encerra nomeando apenas a variável (nunca o valor)."""
    message = f"variável de ambiente {variable} inválida — a subida foi interrompida"
    if hint:
        message = f"{message} ({hint})"
    log("error", "config", "config_invalid", text=message)
    raise SystemExit(message)


def _read_secret_file(environ: Mapping[str, str], name: str) -> str:
    """Lê ``NOME_FILE`` removendo UMA newline final.

    Falha de leitura (arquivo ausente, sem permissão, não-UTF-8) é erro próprio
    nomeando a variável _FILE — o conteúdo do arquivo e o caminho nunca chegam
    ao log nem à exceção (K2; arquitetura linha 192).
    """
    file_variable = name + _FILE_SUFFIX
    try:
        value = Path(environ[file_variable]).read_text(encoding="utf-8")
    except OSError, ValueError:
        _fail(file_variable, "o arquivo do segredo não pôde ser lido")
    return value[:-1] if value.endswith("\n") else value


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    """Lê e valida o ambiente; qualquer falha impede a subida (fail-fast)."""
    secrets: dict[str, str] = {}

    def resolve(name: str) -> str | None:
        """Ordem NOME → NOME_FILE → padrão do chamador (arquitetura linha 190).

        NOME direto vence; via NOME_FILE o valor também vai para ``secrets``.
        """
        if name in environ:
            return environ[name]
        if name + _FILE_SUFFIX in environ:
            value = _read_secret_file(environ, name)
            secrets[name] = value
            return value
        return None

    raw_data_dir = resolve("DATA_DIR")
    if raw_data_dir is None:  # padrão só na ausência real — string vazia é valor inválido
        raw_data_dir = "/data"
    if not raw_data_dir.strip():
        _fail("DATA_DIR", "o caminho não pode ser vazio")
    data_dir = Path(raw_data_dir)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        # Nome por pid: web e worker sobem juntos (D-03) e cada processo só
        # enxerga e limpa o próprio probe — sem corrida write/unlink entre eles.
        probe = data_dir / f".baixou-write-probe.{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError:
        _fail("DATA_DIR", "a pasta não pôde ser criada nem gravada")

    tz_market = resolve("TZ_MARKET")
    if tz_market is None:  # ausente → padrão; string vazia segue para a validação
        tz_market = "America/Sao_Paulo"
    try:
        ZoneInfo(tz_market)  # nome de zona IANA, nunca offset (Pitfall 5)
    except ZoneInfoNotFoundError, ValueError, KeyError:
        _fail(
            "TZ_MARKET",
            "use o nome de uma zona IANA (ex.: America/Sao_Paulo), nunca um offset",
        )

    raw_poll = resolve("POLL_MINUTES")
    if raw_poll is None:  # ausente → padrão; string vazia falha no int abaixo
        raw_poll = "15"
    try:
        poll_minutes = int(raw_poll.strip())
    except ValueError:
        _fail("POLL_MINUTES", "use um inteiro maior que zero")
    if poll_minutes <= 0:
        _fail("POLL_MINUTES", "use um inteiro maior que zero")

    log_level = resolve("LOG_LEVEL")
    if log_level is None:  # ausente → padrão; string vazia não está no conjunto
        log_level = "info"
    log_level = log_level.strip().lower()
    if log_level not in _VALID_LOG_LEVELS:
        _fail("LOG_LEVEL", f"use um de: {', '.join(_VALID_LOG_LEVELS)}")

    # Fase 2 (POLL-01/POLL-02): segredo da brapi — resolve() já manda para
    # ``secrets`` quando lido via _FILE; sem validação de conteúdo além do
    # vazio. O valor nunca chega ao log nem à exceção (K2). Ausente → padrão
    # "" (token opcional); string vazia DEFINIDA é inválida (WR-01).
    raw_token = resolve("BRAPI_TOKEN")
    brapi_token = "" if raw_token is None else raw_token
    if raw_token is not None and not brapi_token.strip():
        _fail("BRAPI_TOKEN", "defina o token da brapi ou remova a variável")

    raw_limit = resolve("BRAPI_MONTHLY_LIMIT")
    if raw_limit is None:  # ausente → padrão; string vazia falha no int abaixo
        raw_limit = "15000"
    try:
        brapi_monthly_limit = int(raw_limit.strip())
    except ValueError:
        _fail("BRAPI_MONTHLY_LIMIT", "use um inteiro maior que zero")
    if brapi_monthly_limit <= 0:
        _fail("BRAPI_MONTHLY_LIMIT", "use um inteiro maior que zero")

    raw_cadence = resolve("B3_EVERY_N_ROUNDS")
    if raw_cadence is None:  # ausente → padrão (PRD: consultar toda rodada, D-01)
        raw_cadence = "1"
    try:
        b3_every_n_rounds = int(raw_cadence.strip())
    except ValueError:  # "1.5" e "abc" caem aqui
        _fail("B3_EVERY_N_ROUNDS", "use um inteiro maior ou igual a 1")
    if b3_every_n_rounds < 1:
        _fail("B3_EVERY_N_ROUNDS", "use um inteiro maior ou igual a 1")

    # Fase 3 (D-14): limiares da máquina de estados — molde exato do
    # BRAPI_MONTHLY_LIMIT aplicado às cinco variáveis: ausente → padrão;
    # string vazia definida falha no int(); valor <= 0 é recusado — sempre
    # nomeando a variável, nunca o valor (K2).
    def positive_int(name: str, default: str) -> int:
        raw = resolve(name)
        if raw is None:  # ausente → padrão; string vazia definida falha no int abaixo
            raw = default
        try:
            value = int(raw.strip())
        except ValueError:
            _fail(name, "use um inteiro maior que zero")
        if value <= 0:
            _fail(name, "use um inteiro maior que zero")
        return value

    rearm_pct = positive_int("REARM_PCT", "1")
    suspect_pct = positive_int("SUSPECT_PCT", "20")
    stale_minutes_b3 = positive_int("STALE_MINUTES_B3", "75")
    stale_minutes_crypto = positive_int("STALE_MINUTES_CRYPTO", "30")
    fail_notify_after = positive_int("FAIL_NOTIFY_AFTER", "3")

    # Segredos de canal — molde exato do BRAPI_TOKEN: resolve() já leva a
    # leitura via _FILE para ``secrets`` e a variável direta vence o arquivo.
    # Ausente → ""; definida e vazia/só-espaços é inválida (WR-01).
    raw_telegram_token = resolve("TELEGRAM_TOKEN")
    telegram_token = "" if raw_telegram_token is None else raw_telegram_token
    if raw_telegram_token is not None and not telegram_token.strip():
        _fail("TELEGRAM_TOKEN", "defina o valor ou remova a variável")

    raw_ntfy_topic = resolve("NTFY_TOPIC")
    ntfy_topic = "" if raw_ntfy_topic is None else raw_ntfy_topic
    if raw_ntfy_topic is not None and not ntfy_topic.strip():
        _fail("NTFY_TOPIC", "defina o valor ou remova a variável")

    raw_ntfy_token = resolve("NTFY_TOKEN")
    ntfy_token = "" if raw_ntfy_token is None else raw_ntfy_token
    if raw_ntfy_token is not None and not ntfy_token.strip():
        _fail("NTFY_TOKEN", "defina o valor ou remova a variável")

    # TELEGRAM_CHAT_ID pelo mesmo molde, mas NÃO é segredo — sem _FILE.
    raw_chat_id = environ.get("TELEGRAM_CHAT_ID")
    telegram_chat_id = "" if raw_chat_id is None else raw_chat_id
    if raw_chat_id is not None and not telegram_chat_id.strip():
        _fail("TELEGRAM_CHAT_ID", "defina o valor ou remova a variável")

    # NTFY_URL tem padrão público (arquitetura, D-14); o formato http(s) em si
    # é validado na checagem cruzada de formato (D-09) — aqui só o vazio.
    raw_ntfy_url = environ.get("NTFY_URL")
    ntfy_url = "https://ntfy.sh" if raw_ntfy_url is None else raw_ntfy_url
    if raw_ntfy_url is not None and not ntfy_url.strip():
        _fail("NTFY_URL", "defina o valor ou remova a variável")

    # NOTIFY_CLICK_URL: ausente ou vazia é VÁLIDO (D-14) — padrão vazio, sem
    # botão de abrir; única variável da fase sem falha de string vazia.
    raw_click_url = environ.get("NOTIFY_CLICK_URL")
    notify_click_url = "" if raw_click_url is None else raw_click_url

    # Pares de canal (D-08): configuração pela metade é config inválida —
    # fail-fast nomeando a variável que FALTA. NTFY_TOPIC sozinho é VÁLIDO
    # (o token é opcional). Sem NENHUM canal configurado o worker sobe normal
    # (avisos desligados). Erros só detectáveis em rede (token revogado,
    # tópico inexistente, 4xx/5xx) NUNCA falham a subida — aparecem como
    # falha de canal no primeiro envio (D-09).
    if telegram_token and not telegram_chat_id:
        _fail("TELEGRAM_CHAT_ID", "TELEGRAM_TOKEN definido exige o chat_id do canal")
    if telegram_chat_id and not telegram_token:
        _fail("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID definido exige o token do bot")
    if ntfy_token and not ntfy_topic:
        _fail("NTFY_TOPIC", "NTFY_TOKEN definido exige o tópico do canal")

    # Formato leve, sem chamar API (D-09): só checagens de string sobre o
    # próprio valor — a mensagem nomeia a variável, nunca o valor (K2).
    if telegram_chat_id:
        try:
            int(telegram_chat_id.strip())  # negativo de grupo é permitido
        except ValueError:
            _fail("TELEGRAM_CHAT_ID", "use o chat_id numérico do bot (negativo para grupos)")
    if telegram_token and ":" not in telegram_token:
        _fail("TELEGRAM_TOKEN", "token do bot no formato <id>:<hash> (falta o ':')")
    if not (ntfy_url.startswith("http://") or ntfy_url.startswith("https://")):
        _fail("NTFY_URL", "use um endereço http:// ou https://")

    # Qualquer NOME_FILE no ambiente é resolvido — mesmo sem campo próprio em
    # Settings (fases futuras adicionam nomes; variáveis desconhecidas são
    # toleradas). NOME presente vence: o arquivo nem é lido.
    for variable in environ:
        if variable.endswith(_FILE_SUFFIX):
            base = variable[: -len(_FILE_SUFFIX)]
            if base and base not in environ and base not in secrets:
                resolve(base)

    return Settings(
        data_dir=data_dir,
        tz_market=tz_market,
        poll_minutes=poll_minutes,
        log_level=log_level,
        brapi_token=brapi_token,
        brapi_monthly_limit=brapi_monthly_limit,
        b3_every_n_rounds=b3_every_n_rounds,
        rearm_pct=rearm_pct,
        suspect_pct=suspect_pct,
        stale_minutes_b3=stale_minutes_b3,
        stale_minutes_crypto=stale_minutes_crypto,
        fail_notify_after=fail_notify_after,
        telegram_token=telegram_token,
        telegram_chat_id=telegram_chat_id,
        ntfy_url=ntfy_url,
        ntfy_topic=ntfy_topic,
        ntfy_token=ntfy_token,
        notify_click_url=notify_click_url,
        secrets=secrets,
    )
