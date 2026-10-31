"""Backup noturno do banco por VACUUM INTO — tarefa do laço do worker (D-05/D-06/D-08).

Execução do SC-4 sobre a fundação pura do 06-04 (``Settings.backup_*`` +
``is_backup_due``). Chamado pelo ``serve_until`` LOGO APÓS cada rodada — sem
serviço novo no compose, sem cron do host (proibido pela arquitetura).

Fluxo (``run_backup``): (1) ``BACKUP_ENABLED=0`` desliga na exceção — nenhum
arquivo em nenhuma hora; (2) o diretório efetivo é ``BACKUP_DIR`` ou, quando
None, ``<DATA_DIR>/backups`` — a derivação é daqui, papel do CONSUMIDOR
(decisão do 06-04: config resolve env, nunca política de caminho); (3) o
destino é ``baixou-YYYY-MM-DD.db`` com a data LOCAL no fuso ``TZ_MARKET``;
(4) destino existente → skip: O ARQUIVO DATADO É O ESTADO (D-06) — reinício
do worker no meio do dia não duplica e NÃO há migração/coluna nova porque o
estado "backup de hoje já foi feito" é derivado do SISTEMA DE ARQUIVOS, não
do banco; (5) marcador de tentativa do dia existente → skip: UMA TENTATIVA
POR NOITE civil (D-08) — o ``.backup-attempt-YYYY-MM-DD`` em DATA_DIR é o
estado da tentativa e é gravado ANTES do trabalho de backup, então a noite
que FALHOU também registrou a tentativa e não re-tenta nas rodadas de 15 min
seguintes do mesmo dia (sem isso o destino falho nunca nasce, cada rodada
re-tentaria e o contador contaria RODADAS, não noites — gap CR-04); (6)
antes da hora local marcada → nada acontece (``is_backup_due``, borda
inclusiva); (7) ``VACUUM INTO`` REAL com binding de parâmetro, FORA de
qualquer ``write_tx`` — disciplina WAL (T-02-01/T-06-06): o I/O de disco do
backup NUNCA segura o lock do SQLite (a conexão do worker nasce em
``isolation_level=None``, então o execute é autocommit direto); (8) no
sucesso, limpeza best-effort dos marcadores de dias anteriores, retenção por
NOME (abaixo) e a transição de recovery do episódio; (9) em exceção
QUALQUER: log ``backup_failed`` com kind, contador consecutivo de NOITES em
memória de módulo (auxiliado pelo marcador persistido no volume: REINÍCIO
ZERA a memória — aceito: backup é noturno, D-08 — mas NÃO re-tenta o dia) e,
no limiar ``BACKUP_FAIL_NOTIFY_AFTER``, UM aviso no canal.

Falha de backup NUNCA derruba a rodada nem o worker (SC-3): o módulo captura
a própria exceção e o call-site do ``serve_until`` é a segunda cinta. O aviso
de falha usa o MESMO canal dos avisos de preço e NUNCA os paralisa: envio
FORA de transação com um ``httpx.Client`` de vida curta criado localmente
(fechado no ``finally``), gravação do episódio em write_tx curta própria.

A máquina de episódio (``backup:failure``/``backup:recovery``) REPLICA o
molde de ``worker.alerts`` (Fase 3, ALERT-03/D-08 — escolha do usuário contra
só-log): pendente (linha ausente ou delivered=0) re-tenta a cada noite até o
primeiro aceite; entregue, nunca mais no episódio; a transição de recovery
APAGA a chave de falha e UPSERTA a de recovery — um SEGUNDO ciclo
falha→sucesso emite os dois avisos DE NOVO (um aviso por EPISÓDIO). O molde
é replicado, nunca importado: as funções privadas de ``alerts`` pertencem ao
episódio de fonte/cota, e o backup não pode acoplar-se a elas (mesma razão
pela qual ``main`` e ``alerts`` replicam ``_parse_ts``).

Retenção (D-06): ``BACKUP_KEEP`` dias — arquivos cujo nome casa com
``baixou-YYYY-MM-DD.db`` e cuja DATA NO NOME é mais antiga que o limite são
apagados; a comparação é pelo NOME, nunca por mtime (renomear/tocar um
arquivo não muda a retenção), e arquivos FORA do padrão (cópia manual, .env)
ficam INTOCADOS.

Eventos: ``backup_completed`` (nome do arquivo), ``backup_pruned`` (contagem
da retenção), ``backup_failed`` (kind — nunca o caminho nem o conteúdo, K2),
``backup_notify_alert`` e ``backup_delivery_failed`` (avisos do episódio).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from baixou.core.config import Settings
from baixou.core.db import write_tx
from baixou.core.log import log
from baixou.core.schedule import is_backup_due
from baixou.worker import messages
from baixou.worker.notifiers.base import NotificationResult, configured_channels

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# Padrão do arquivo de backup: baixou-YYYY-MM-DD.db (D-06) — a retenção só
# toca arquivos que casam EXATAMENTE com o padrão; o resto é intocado.
_BACKUP_NAME_RE = re.compile(r"^baixou-(\d{4}-\d{2}-\d{2})\.db$")

# Marcador da tentativa do dia (D-08): <DATA_DIR>/.backup-attempt-YYYY-MM-DD —
# a EXISTÊNCIA do arquivo (0 bytes, só o nome importa) diz "a tentativa de
# hoje — sucesso OU falha — já aconteceu". É o estado que limita o backup a
# UMA tentativa por dia civil mesmo quando o destino falho nunca nasce
# (arquivo datado é o estado do SUCESSO; o marcador é o estado da TENTATIVA).
_ATTEMPT_PREFIX = ".backup-attempt-"

# Chaves do episódio de aviso (D-08) — mesmas da docstring do must_have.
_FAILURE_KEY = "backup:failure"
_RECOVERY_KEY = "backup:recovery"

# Timeout do cliente httpx de vida curta do aviso de falha (envio FORA de
# transação; o cliente é criado localmente e fechado no finally).
_NOTIFY_TIMEOUT_S = 10.0

# Contador consecutivo de NOITES com falha — memória de MÓDULO auxiliada pelo
# marcador persistido (o gate ``.backup-attempt-<dia>`` limita a UMA tentativa
# por dia civil, então cada incremento é uma NOITE, não uma rodada — gap
# CR-04). Um reinício do worker zera a memória (aceito em D-08: backup é
# noturno, o episódio no banco continua e o limiar reencontra o episódio
# pendente/entregue) mas NÃO re-tenta o dia.
_consecutive_failures = 0


def _episode(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    """Lê a linha do episódio (leitura fora de transação — disciplina WAL)."""
    return conn.execute("SELECT delivered FROM notify_episode WHERE key = ?", (key,)).fetchone()


def _episode_pending(conn: sqlite3.Connection, key: str) -> bool:
    """Episódio sem entrega (D-06): linha AUSENTE (não iniciado) ou delivered=0."""
    row = _episode(conn, key)
    return row is None or not row["delivered"]


def _upsert_episode(conn: sqlite3.Connection, key: str, delivered: bool, now_utc: datetime) -> None:
    """UPSERT da flag de ENTREGA — chamar DENTRO de write_tx (T-02-01).

    delivered=1 SÓ com aceite de ≥1 canal (HTTP 2xx — D-16); 0 deixa pendente
    (a noite seguinte tenta de novo). ``updated_at`` em UTC ISO-8601.
    """
    conn.execute(
        """
        INSERT INTO notify_episode (key, delivered, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT (key) DO UPDATE SET
            delivered = excluded.delivered,
            updated_at = excluded.updated_at
        """,
        (key, 1 if delivered else 0, now_utc.strftime(_TS_FORMAT)),
    )


def _record_episode(conn: sqlite3.Connection, key: str, delivered: bool, now_utc: datetime) -> None:
    """Grava a flag do episódio em write_tx curta própria (T-02-01)."""
    with write_tx(conn):
        _upsert_episode(conn, key, delivered, now_utc)


def _send_all(
    channels: list[tuple[str, Callable]],
    settings: Settings,
    client: httpx.Client,
    title: str,
    body: str,
) -> bool:
    """Envia o aviso por TODOS os canais em sequência — rede FORA de transação (T-02-01).

    Devolve True quando ≥1 canal aceita (HTTP 2xx — D-16); cada recusa emite
    ``backup_delivery_failed`` citando só kind e código HTTP (K2). Sem retry
    dentro da noite: a repetição é na noite seguinte (D-06).
    """
    delivered = False
    for channel, send in channels:
        result: NotificationResult = send(client, settings, title, body)
        if result.ok:
            delivered = True
        else:
            log(
                "warning",
                "backup",
                "backup_delivery_failed",
                channel=channel,
                kind=result.kind,
                http_status=result.http_status,
                text=f"falha de entrega no canal {channel}: {result.kind}",
            )
    return delivered


def _deliver_episode_message(
    conn: sqlite3.Connection,
    settings: Settings,
    title: str,
    body: str,
    now_utc: datetime,
) -> bool:
    """Entrega um aviso do episódio com cliente httpx local de vida curta (D-08).

    O cliente nasce AQUI (o módulo não compartilha o cliente da rodada — o
    backup é chamado pelo laço, fora do ``with httpx.Client`` de ``run_round``)
    e é fechado no ``finally``; o envio fica FORA de qualquer transação.
    """
    channels = configured_channels(settings)
    client = httpx.Client(timeout=_NOTIFY_TIMEOUT_S)
    try:
        return _send_all(channels, settings, client, title, body)
    finally:
        client.close()


def _notify_failure(conn: sqlite3.Connection, settings: Settings, now_utc: datetime) -> None:
    """Aviso do episódio de FALHA (D-08) — molde de ``alerts._source_failure``.

    Dispara ao alcançar ``BACKUP_FAIL_NOTIFY_AFTER`` (borda inclusiva) com o
    episódio PENDENTE (linha ausente ou delivered=0 — pendente re-tenta a cada
    noite até o primeiro aceite, D-06); entregue, nunca mais NO episódio.
    """
    if _consecutive_failures < settings.backup_fail_notify_after:
        return  # abaixo do limiar: só log e contador
    if not _episode_pending(conn, _FAILURE_KEY):
        return  # episódio já entregue: um aviso por episódio (D-06/D-08)
    title, body = messages.backup_failure_message(_consecutive_failures)
    delivered = _deliver_episode_message(conn, settings, title, body, now_utc)
    _record_episode(conn, _FAILURE_KEY, delivered, now_utc)
    log(
        "info" if delivered else "warning",
        "backup",
        "backup_notify_alert",
        episode="failure",
        delivered=delivered,
        nights=_consecutive_failures,
        text=f"aviso de backup com falha ({_consecutive_failures} noites seguidas)",
    )


def _recover_episode(conn: sqlite3.Connection, settings: Settings, now_utc: datetime) -> None:
    """Episódio de RECUPERAÇÃO do backup (D-08) — molde de ``alerts._source_recovery``.

    TRANSIÇÃO (a chave de falha EXISTE — pendente ou entregue): envia o aviso
    de volta ao normal e, numa write_tx curta própria, APAGA a chave de falha
    (encerrando SEM envio o aviso pendente — reenviar "backup com falha"
    depois do sucesso seria informação falsa) e UPSERTA a de recovery. O
    delete É o reset que abre o PRÓXIMO episódio: um SEGUNDO ciclo
    falha→sucesso emite os dois avisos DE NOVO (ALERT-03). REENVIO PENDENTE
    (chave de falha ausente, recovery delivered=0): repete até o aceite.
    Com a de recovery ausente ou entregue: episódio fechado — nada.
    """
    if _episode(conn, _FAILURE_KEY) is not None:  # TRANSIÇÃO: episódio em curso
        title, body = messages.backup_recovery_message()
        delivered = _deliver_episode_message(conn, settings, title, body, now_utc)
        with write_tx(conn):
            conn.execute("DELETE FROM notify_episode WHERE key = ?", (_FAILURE_KEY,))
            _upsert_episode(conn, _RECOVERY_KEY, delivered, now_utc)
        _log_recovery(delivered)
        return
    row = _episode(conn, _RECOVERY_KEY)
    if row is None or row["delivered"]:
        return  # episódio fechado (nunca iniciado ou já entregue)
    title, body = messages.backup_recovery_message()  # REENVIO PENDENTE
    delivered = _deliver_episode_message(conn, settings, title, body, now_utc)
    _record_episode(conn, _RECOVERY_KEY, delivered, now_utc)
    _log_recovery(delivered)


def _log_recovery(delivered: bool) -> None:
    log(
        "info" if delivered else "warning",
        "backup",
        "backup_notify_alert",
        episode="recovery",
        delivered=delivered,
        text="aviso de volta ao normal do backup noturno",
    )


def _prune_old_backups(backup_dir: Path, today_local: date, keep_days: int) -> int:
    """Apaga excedentes da retenção — decide pela DATA NO NOME, nunca mtime (D-06).

    Só arquivos que casam com ``baixou-YYYY-MM-DD.db`` entram na conta: o nome
    mais antigo que ``keep_days`` dias (borda INCLUSIVA — exatamente no limite
    fica, precedente D-10/D-12) é apagado; nomes fora do padrão (cópia manual,
    .env no mesmo diretório) ficam INTOCADOS. Devolve quantos apagou.
    """
    pruned = 0
    for candidate in backup_dir.iterdir():
        if not candidate.is_file():
            continue
        matched = _BACKUP_NAME_RE.match(candidate.name)
        if matched is None:
            continue  # fora do padrão: não é nosso — intocado (D-06)
        file_date = date.fromisoformat(matched.group(1))
        if (today_local - file_date).days > keep_days:
            candidate.unlink()
            pruned += 1
    return pruned


def _attempt_marker(data_dir: Path, local_date: date) -> Path:
    """Caminho do marcador da tentativa do dia em DATA_DIR (D-08).

    ``<DATA_DIR>/.backup-attempt-<data local>`` — a MESMA data local no fuso
    ``TZ_MARKET`` já computada para o nome do destino. O ARQUIVO DATADO É O
    ESTADO da TENTATIVA: gravado DENTRO do try e ANTES do mkdir do
    backup_dir, registra a noite mesmo quando o backup falha — o cenário
    exato do gap (destino que nunca nasce re-tentaria a cada rodada de 15
    min e inflaria o contador a "noites" inexistentes).
    """
    return data_dir / f"{_ATTEMPT_PREFIX}{local_date.isoformat()}"


def _prune_attempt_markers(data_dir: Path, today_local: date) -> None:
    """Apaga marcadores de tentativa de dias anteriores — limpeza best-effort (T-06-10).

    O nome DATADO limita a um marcador por dia no máximo (T-06-10: DoS por
    acúmulo); a limpeza mantém DATA_DIR enxuto sem tabela nem migração. NUNCA
    propaga: falha de limpeza não vira ``backup_failed`` nem incrementa o
    contador — é higiene, não estado de segurança (T-06-10).
    """
    try:
        for candidate in data_dir.glob(f"{_ATTEMPT_PREFIX}*"):
            suffix = candidate.name[len(_ATTEMPT_PREFIX) :]
            try:
                marker_date = date.fromisoformat(suffix)
            except ValueError:
                continue  # nome fora do padrão: não é nosso — intocado
            if marker_date != today_local:
                candidate.unlink(missing_ok=True)
    except OSError:
        pass  # limpeza falhada: silêncio — nunca entra na conta de falhas


def run_backup(conn: sqlite3.Connection, settings: Settings, now_utc: datetime) -> None:
    """Backup noturno da rodada: VACUUM INTO fora de write_tx + retenção + episódio (SC-4).

    Chamado pelo ``serve_until`` após CADA rodada concluída. Não levanta: toda
    exceção do processo de backup é capturada, logada
    (``backup_failed`` + kind, nunca caminho nem conteúdo — K2), acumulada no
    contador de episódio e, no limiar, avisada no canal — a rodada que chamou
    NUNCA é afetada (SC-3). ``now_utc`` (aware) define o dia local do arquivo
    e os timestamps do episódio — injetável para testes sem relógio congelado.
    UMA tentativa por NOITE civil: o marcador ``.backup-attempt-<dia>`` em
    DATA_DIR bloqueia a re-tentativa no mesmo dia (sucesso ou falha), então o
    contador conta NOITES — o problema consertado no meio do dia só gera
    backup na noite seguinte.
    """
    global _consecutive_failures

    if not settings.backup_enabled:
        return  # BACKUP_ENABLED=0 desliga na exceção (D-07): nenhuma chamada cria arquivo
    tz = ZoneInfo(settings.tz_market)
    backup_dir = (
        settings.backup_dir
        if settings.backup_dir is not None
        else settings.data_dir / "backups"  # derivação é papel do CONSUMIDOR (06-04)
    )
    local_date = now_utc.astimezone(tz).date()
    destination = backup_dir / f"baixou-{local_date.isoformat()}.db"
    if destination.exists():
        # O ARQUIVO DATADO É O ESTADO (D-06): "backup de hoje já foi feito" é
        # derivado do sistema de arquivos — reinício no meio do dia não
        # duplica e nenhuma migração/coluna nova é necessária. Além disso o
        # VACUUM INTO EXIGE destino inexistente (D-06): o skip é obrigatório.
        return
    marker = _attempt_marker(settings.data_dir, local_date)
    if marker.exists():
        # A TENTATIVA DE HOJE — sucesso OU falha — já aconteceu (D-08: UMA
        # tentativa por NOITE civil). Sem este gate, um backup falho nunca
        # cria o destino e cada rodada de 15 min re-tentaria: o contador
        # contaria RODADAS, não NOITES (gap CR-04), e o aviso "N noites
        # seguidas" chegaria ~30 min dentro da primeira noite ruim, com uma
        # noite inteira de outage inflando a conta a ~80. O marcador persiste
        # no volume: reinício zera a memória do contador (aceito, D-08) mas
        # NÃO re-tenta o dia.
        return
    if not is_backup_due(now_utc, settings.backup_hour, tz):
        return  # antes da hora local marcada: nada acontece (borda inclusiva, 06-04)

    try:
        # Limpeza best-effort dos marcadores de dias anteriores (T-06-10):
        # try/except PRÓPRIO e silencioso dentro do helper — nunca vira
        # backup_failed nem incrementa o contador.
        _prune_attempt_markers(settings.data_dir, local_date)
        # O MARCADOR É O ESTADO DA TENTATIVA: gravado DENTRO do try e ANTES
        # do mkdir do backup_dir — a falha de backup_dir (o cenário exato do
        # gap) também registra a tentativa do dia e para de re-tentar na
        # mesma noite. Único caso em que a conta por noite degrada a
        # por-rodada: DATA_DIR indisponível depois do boot faz o marcador
        # não nascer e as rodadas voltam a contar — aceito: storage do
        # worker morto é falha real e o aviso cedo é o correto.
        marker.touch()
        backup_dir.mkdir(parents=True, exist_ok=True)  # no primeiro uso (D-05)
        # VACUUM INTO FORA de write_tx (T-06-06 — mitigação do T-06-06 do plano):
        # o I/O de disco do backup NUNCA segura o lock do SQLite (disciplina WAL,
        # T-02-01). A conexão do worker está em isolation_level=None: execute
        # direto, sem transação; binding de parâmetro evita interpolação de path.
        conn.execute("VACUUM INTO ?", (str(destination),))
        pruned = _prune_old_backups(backup_dir, local_date, settings.backup_keep)
        if pruned:
            log(
                "info",
                "backup",
                "backup_pruned",
                count=pruned,
                kept=settings.backup_keep,
                text=f"retenção: {pruned} backup(s) antigo(s) apagado(s)",
            )
        log(
            "info",
            "backup",
            "backup_completed",
            file=destination.name,
            text="backup noturno concluído",
        )
        _consecutive_failures = 0  # sucesso zera a conta de noites com falha
        _recover_episode(conn, settings, now_utc)
    except Exception as exc:  # NUNCA propaga (SC-3): a rodada que chamou segue
        kind = type(exc).__name__
        log(
            "error",
            "backup",
            "backup_failed",
            kind=kind,
            text=f"backup noturno falhou ({kind}); a rodada segue — o aviso de preço não é afetado",
        )
        # +1 NOITE (não rodada): o gate do marcador limita a uma tentativa por
        # dia civil. Reinício zera a memória — aceito (backup é noturno, D-08).
        _consecutive_failures += 1
        try:
            _notify_failure(conn, settings, now_utc)
        except Exception:  # a segunda cinta do aviso: episódio nunca derruba nada
            log(
                "error",
                "backup",
                "backup_notify_failed",
                text="falha ao registrar o episódio de backup; a rodada segue",
            )
