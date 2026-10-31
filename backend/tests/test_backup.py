"""Backup noturno no laço do worker — VACUUM INTO, retenção e episódio de falha (D-05/D-06/D-08).

Sem rede: VACUUM INTO REAL em ``tmp_path`` (o arquivo de backup abre como SQLite
e contém as tabelas do projeto), canais via ``httpx.MockTransport`` injetado no
``httpx.Client`` que o módulo cria localmente e relógio INJETADO pela própria
API — ``run_backup(conn, settings, now_utc)`` recebe o instante por parâmetro
(decisão D-14 do CONTEXT: "backup com relógio injetável"), então cada teste
fixa a hora civil sem monkeypatch de relógio.

Contratos provados aqui (must_haves do plano 06-06):

- a primeira rodada APÓS ``BACKUP_HOUR`` no fuso ``TZ_MARKET`` cria
  ``baixou-YYYY-MM-DD.db`` via VACUUM REAL FORA de write_tx (T-06-06);
- o ARQUIVO DATADO É O ESTADO — destino existente → skip (reinício no meio do
  dia não duplica, D-06), sem migração nova;
- retenção decide pela DATA NO NOME (nunca mtime) e arquivos fora do padrão
  ``baixou-YYYY-MM-DD.db`` ficam intocados;
- falha de backup NUNCA propaga (SC-3) — log ``backup_failed`` com kind,
  contador consecutivo sobe em memória de módulo (reinício zera — aceito,
  backup é noturno);
- ``BACKUP_FAIL_NOTIFY_AFTER`` noites seguidas → UM aviso no canal
  (``backup:failure``) e um único volta-ao-normal na recuperação
  (``backup:recovery``) — molde de episódio da Fase 3 (ALERT-03): um SEGUNDO
  ciclo falha→sucesso re-emite os dois (um aviso por EPISÓDIO);
- ``BACKUP_ENABLED=0`` desliga na exceção: nenhum arquivo em nenhuma hora.

O import de ``baixou.worker.backup`` acontece DENTRO do corpo de cada teste
(precedente 06-04): no RED o falho é do teste-alvo, nunca da coleta.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from baixou.core.config import Settings, load_settings
from baixou.core.db import WORKER_TIMEOUT_S, connect
from baixou.core.migrations import apply_migrations

_TZ = ZoneInfo("America/Sao_Paulo")  # UTC-3 o ano todo (sem DST desde 2019)

# Quinta-feira 2026-10-24: 08:00 UTC = 05:00 locais (após a hora padrão 4);
# 06:00 UTC = 03:00 locais (antes da hora marcada — nada acontece).
_AFTER_HOUR = datetime(2026, 10, 24, 8, 0, 0, tzinfo=UTC)
_LATER_SAME_DAY = datetime(2026, 10, 24, 13, 0, 0, tzinfo=UTC)  # 10:00 locais
_BEFORE_HOUR = datetime(2026, 10, 24, 6, 0, 0, tzinfo=UTC)  # 03:00 locais

_TELEGRAM_TOKEN = "123456789:bot-tok-secreto-xyz"  # formato <id>:<hash> (D-09)
_TELEGRAM_CHAT_ID = "42"

_BACKUP_DAY = "2026-10-24"  # data local de _AFTER_HOUR em _TZ


@pytest.fixture
def backup_env(tmp_path, monkeypatch) -> tuple[Settings, sqlite3.Connection]:
    """Settings de teste + banco recém-migrado (molde ops_env de test_notify_ops.py)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TZ_MARKET", "America/Sao_Paulo")
    monkeypatch.setenv("POLL_MINUTES", "15")
    monkeypatch.setenv("LOG_LEVEL", "info")
    settings = load_settings()
    apply_migrations(settings.db_path)
    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    yield settings, conn
    conn.close()


@pytest.fixture(autouse=True)
def _reset_backup_state():
    """Zera o contador consecutivo de falhas (memória de módulo) entre testes.

    O import é lazy e tolerante à ausência do módulo: no RED o fixture não
    pode falhar (fault de fixture invalida o RED — tdd.md).
    """
    yield
    try:
        from baixou.worker import backup
    except ImportError:
        return
    backup._consecutive_failures = 0


def _canal_falso(
    monkeypatch, requests: list[httpx.Request], *, ok: bool = True
) -> None:
    """Injeta MockTransport no httpx.Client que o módulo backup cria localmente.

    O cliente de aviso nasce DENTRO do módulo (envio FORA de transação, fechado
    no finally — mesmo molde do envio de episódio da Fase 3); o transporte
    falso grava cada requisição em ``requests`` e responde 2xx quando ``ok``.
    """
    from baixou.worker import backup

    real_client = httpx.Client

    def factory(**kwargs):
        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if ok:
                return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})
            return httpx.Response(500, text="boom")

        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(backup.httpx, "Client", factory)


def _pedidos_com(requests: list[httpx.Request], fragmento: str) -> list[httpx.Request]:
    """Requisições ao canal cujo corpo cita o fragmento (título do aviso)."""
    return [r for r in requests if fragmento in json.loads(r.content)["text"]]


def _episodio(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute("SELECT delivered FROM notify_episode WHERE key = ?", (key,)).fetchone()


def _com_canal(settings: Settings, **changes) -> Settings:
    """Settings com o canal Telegram configurado (para os testes de episódio)."""
    return replace(
        settings,
        telegram_token=_TELEGRAM_TOKEN,
        telegram_chat_id=_TELEGRAM_CHAT_ID,
        **changes,
    )


def test_first_round_after_backup_hour_creates_real_sqlite_backup(backup_env, tmp_path, capsys):
    """Primeira rodada após BACKUP_HOUR: VACUUM INTO REAL nasce, abre e tem as tabelas."""
    from baixou.worker import backup  # RED: import no corpo — falho no teste, não na coleta

    settings, conn = backup_env
    alvo = replace(settings, backup_dir=tmp_path / "backups")
    backup.run_backup(conn, alvo, _AFTER_HOUR)

    destino = tmp_path / "backups" / f"baixou-{_BACKUP_DAY}.db"
    assert destino.exists()
    copia = sqlite3.connect(destino)  # abre como SQLite de verdade (VACUUM REAL, T-06-06)
    try:
        tabelas = {
            row[0]
            for row in copia.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    finally:
        copia.close()
    assert {"watch", "run", "notify_episode", "schema_migrations"} <= tabelas
    saida = capsys.readouterr().out
    assert "backup_completed" in saida
    assert f"baixou-{_BACKUP_DAY}.db" in saida  # o log cita o nome do arquivo


def test_dated_file_is_the_state_second_call_same_day_skips(backup_env, tmp_path, capsys):
    """Segunda chamada no MESMO dia com hora maior: destino existe → skip (D-06).

    O teste escreve um marcador DENTRO da cópia: se a segunda chamada
    re-executasse o VACUUM (ou tentasse — destino existente é erro do SQLite),
    o marcador morreria ou sairia backup_failed. O arquivo É o estado.
    """
    from baixou.worker import backup

    settings, conn = backup_env
    alvo = replace(settings, backup_dir=tmp_path / "backups")
    backup.run_backup(conn, alvo, _AFTER_HOUR)
    destino = tmp_path / "backups" / f"baixou-{_BACKUP_DAY}.db"

    marcador = sqlite3.connect(destino)
    try:
        marcador.execute("CREATE TABLE prova_skip (x)")
        marcador.execute("INSERT INTO prova_skip VALUES (1)")
        marcador.commit()
    finally:
        marcador.close()

    backup.run_backup(conn, alvo, _LATER_SAME_DAY)  # mesma data, hora maior

    conferencia = sqlite3.connect(destino)
    try:
        resta = conferencia.execute("SELECT COUNT(*) FROM prova_skip").fetchone()[0]
    finally:
        conferencia.close()
    assert resta == 1  # a cópia NÃO foi refeita
    saida = capsys.readouterr().out
    assert saida.count("backup_completed") == 1  # só a primeira executou
    assert "backup_failed" not in saida  # skip sem log de erro


def test_before_marked_hour_nothing_happens(backup_env, tmp_path, capsys):
    """Chamada ANTES da hora local marcada: is_backup_due False → nada (nem pasta)."""
    from baixou.worker import backup

    settings, conn = backup_env
    pasta = tmp_path / "backups"
    backup.run_backup(conn, replace(settings, backup_dir=pasta), _BEFORE_HOUR)

    assert not (pasta / f"baixou-{_BACKUP_DAY}.db").exists()
    assert not pasta.exists()  # "nada acontece" — nem o diretório nasce
    saida = capsys.readouterr().out
    assert "backup_completed" not in saida
    assert "backup_failed" not in saida


def test_backup_enabled_zero_never_creates_anything(backup_env, tmp_path, capsys):
    """BACKUP_ENABLED=0 desliga na exceção: nenhum arquivo em nenhuma hora."""
    from baixou.worker import backup

    settings, conn = backup_env
    pasta = tmp_path / "backups"
    desligado = replace(settings, backup_enabled=False, backup_dir=pasta)
    backup.run_backup(conn, desligado, _BEFORE_HOUR)
    backup.run_backup(conn, desligado, _AFTER_HOUR)
    backup.run_backup(conn, desligado, _LATER_SAME_DAY)

    assert not pasta.exists()
    saida = capsys.readouterr().out
    assert "backup_completed" not in saida
    assert "backup_failed" not in saida


def test_default_backup_dir_derives_data_dir_backups(backup_env):
    """backup_dir None → o módulo deriva <DATA_DIR>/backups (papel do consumidor, 06-04)."""
    from baixou.worker import backup

    settings, conn = backup_env
    assert settings.backup_dir is None  # fundação do 06-04: None = derivar aqui
    backup.run_backup(conn, settings, _AFTER_HOUR)

    destino = settings.data_dir / "backups" / f"baixou-{_BACKUP_DAY}.db"
    assert destino.exists()


def test_retention_by_name_keeps_recent_and_spares_foreign_files(backup_env, tmp_path, capsys):
    """BACKUP_KEEP=2 com 4 arquivos datados: mantém os 2 mais recentes PELO NOME.

    Os mtimes são INVERTIDOS de propósito (o mais velho com o mtime mais
    novo): retenção por mtime manteria 10-20 e apagaria 10-23 — sobreviver
    10-23 e morrer 10-20 prova que a decisão é pela DATA NO NOME (D-06).
    Arquivos fora do padrão (cópia manual, .env) ficam INTOCADOS.
    """
    from baixou.worker import backup

    settings, conn = backup_env
    pasta = tmp_path / "backups"
    pasta.mkdir()
    antigos = {
        "baixou-2026-10-20.db": 1_800_000_000.0,  # mtime MAIS novo (armadilha)
        "baixou-2026-10-21.db": 1_700_000_000.0,
        "baixou-2026-10-22.db": 1_600_000_000.0,
        "baixou-2026-10-23.db": 1_500_000_000.0,  # mtime mais velho, nome mais recente
    }
    for nome, mtime in antigos.items():
        caminho = pasta / nome
        caminho.write_bytes(b"sqlite de teste")
        os.utime(caminho, (mtime, mtime))
    (pasta / "copia-manual.db").write_bytes(b"fora do padrao")
    (pasta / ".env").write_text("TELEGRAM_TOKEN=nao-apagar", encoding="utf-8")

    backup.run_backup(
        conn, replace(settings, backup_dir=pasta, backup_keep=2), _AFTER_HOUR
    )  # hoje local = 2026-10-24

    nomes = {p.name for p in pasta.iterdir()}
    assert f"baixou-{_BACKUP_DAY}.db" in nomes  # o backup de hoje nasceu
    assert "baixou-2026-10-23.db" in nomes  # ontem: 1 dia — fica
    assert "baixou-2026-10-22.db" in nomes  # 2 dias: borda inclusiva — fica
    assert "baixou-2026-10-21.db" not in nomes  # 3 dias > KEEP=2 — apagado
    assert "baixou-2026-10-20.db" not in nomes  # 4 dias — apagado (mesmo mtime novo)
    assert "copia-manual.db" in nomes  # fora do padrão: INTOCADO
    assert ".env" in nomes
    saida = capsys.readouterr().out
    assert "backup_pruned" in saida


def test_vacuum_failure_logs_kind_counts_and_never_raises(backup_env, tmp_path, capsys):
    """Falha do backup: log backup_failed com kind, contador sobe, NADA propaga (SC-3)."""
    from baixou.worker import backup

    settings, conn = backup_env
    bloqueio = tmp_path / "bloqueio.txt"
    bloqueio.write_text("não é uma pasta", encoding="utf-8")
    falho = replace(
        settings,
        backup_dir=bloqueio,  # mkdir do primeiro uso explode (FileExistsError)
        backup_fail_notify_after=99,  # longe do limiar: só log e contador
        telegram_token="",
        telegram_chat_id="",
    )

    backup.run_backup(conn, falho, _AFTER_HOUR)  # NENHUMA exceção propaga
    backup.run_backup(conn, falho, _AFTER_HOUR)

    assert backup._consecutive_failures == 2  # contador consecutivo sobe
    saida = capsys.readouterr().out
    assert saida.count("backup_failed") == 2
    assert "FileExistsError" in saida  # o kind vai no log
    assert str(bloqueio) not in saida  # caminho nunca no log (K2)
    assert _episodio(conn, "backup:failure") is None  # abaixo do limiar: sem episódio


def test_failure_threshold_sends_one_channel_notice_and_does_not_repeat(
    backup_env, tmp_path, monkeypatch
):
    """BACKUP_FAIL_NOTIFY_AFTER noites: UM aviso no canal; o episódio não repete (D-08).

    Chaves EXATAS do episódio lidas de notify_episode: backup:failure.
    """
    from baixou.worker import backup

    settings, conn = backup_env
    bloqueio = tmp_path / "bloqueio.txt"
    bloqueio.write_text("não é uma pasta", encoding="utf-8")
    falho = _com_canal(
        settings, backup_dir=bloqueio, backup_fail_notify_after=2
    )
    pedidos: list[httpx.Request] = []
    _canal_falso(monkeypatch, pedidos)

    backup.run_backup(conn, falho, _AFTER_HOUR)  # noite 1: counter 1 < 2
    assert _pedidos_com(pedidos, "backup") == []
    assert backup._consecutive_failures == 1

    backup.run_backup(conn, falho, _AFTER_HOUR)  # noite 2: counter 2 ≥ 2 → aviso
    avisos = _pedidos_com(pedidos, "backup")
    assert len(avisos) == 1
    corpo = json.loads(avisos[0].content)["text"]
    assert "2 noites seguidas" in corpo  # corpo PT-BR do backup_failure_message
    assert _episodio(conn, "backup:failure")["delivered"] == 1  # canal aceitou

    backup.run_backup(conn, falho, _AFTER_HOUR)  # noite 3: mesmo episódio — NÃO repete
    assert len(_pedidos_com(pedidos, "backup")) == 1
    assert backup._consecutive_failures == 3


def test_success_after_failure_episode_sends_recovery_and_second_cycle_repeats_both(
    backup_env, tmp_path, monkeypatch
):
    """Sucesso após episódio de falha ENTREGUE: UM volta-ao-normal; 2º ciclo repete os DOIS.

    Molde ALERT-03 (um aviso por EPISÓDIO): a transição de recuperação apaga a
    chave de falha e grava backup:recovery — o ciclo seguinte falha→sucesso
    emite os dois avisos DE NOVO.
    """
    from baixou.worker import backup

    settings, conn = backup_env
    bloqueio = tmp_path / "bloqueio.txt"
    bloqueio.write_text("não é uma pasta", encoding="utf-8")
    boa = tmp_path / "backups-ok"
    falho = _com_canal(settings, backup_dir=bloqueio, backup_fail_notify_after=2)
    bom = _com_canal(settings, backup_dir=boa)
    pedidos: list[httpx.Request] = []
    _canal_falso(monkeypatch, pedidos)

    dia1 = datetime(2026, 10, 24, 8, 0, 0, tzinfo=UTC)
    dia2 = datetime(2026, 10, 25, 8, 0, 0, tzinfo=UTC)
    dia3 = datetime(2026, 10, 26, 8, 0, 0, tzinfo=UTC)
    dia4 = datetime(2026, 10, 27, 8, 0, 0, tzinfo=UTC)

    backup.run_backup(conn, falho, dia1)  # noite 1
    backup.run_backup(conn, falho, dia1)  # noite 2 → aviso de falha entregue
    assert len(_pedidos_com(pedidos, "backup")) == 1
    backup.run_backup(conn, bom, dia2)  # noite 3: sucesso → volta ao normal
    recuperacoes = _pedidos_com(pedidos, "normalizado")
    assert len(recuperacoes) == 1
    assert _episodio(conn, "backup:failure") is None  # chave de falha APAGADA
    assert _episodio(conn, "backup:recovery")["delivered"] == 1

    backup.run_backup(conn, bom, dia2)  # sucesso repetido no dia: episódio fechado
    assert len(_pedidos_com(pedidos, "normalizado")) == 1

    # SEGUNDO ciclo falha→sucesso NO MESMO banco: os dois avisos DE NOVO.
    backup.run_backup(conn, falho, dia3)  # noite 1 do ciclo 2
    backup.run_backup(conn, falho, dia3)  # noite 2 → NOVO aviso de falha
    assert len(_pedidos_com(pedidos, "backup")) == 2
    backup.run_backup(conn, bom, dia4)  # sucesso → NOVA recuperação
    assert len(_pedidos_com(pedidos, "normalizado")) == 2
    assert _episodio(conn, "backup:failure") is None
    assert _episodio(conn, "backup:recovery")["delivered"] == 1


def test_no_channels_records_delivered_false_without_error(backup_env, tmp_path, monkeypatch):
    """Sem canal configurado: episódios gravam delivered=False e NADA é levantado."""
    from baixou.worker import backup

    settings, conn = backup_env
    bloqueio = tmp_path / "bloqueio.txt"
    bloqueio.write_text("não é uma pasta", encoding="utf-8")
    boa = tmp_path / "backups-ok"
    falho = replace(
        settings,
        backup_dir=bloqueio,
        backup_fail_notify_after=1,  # limiar na primeira falha
        telegram_token="",
        telegram_chat_id="",  # nenhum canal configurado
    )
    bom = replace(settings, backup_dir=boa, telegram_token="", telegram_chat_id="")
    pedidos: list[httpx.Request] = []
    _canal_falso(monkeypatch, pedidos)  # qualquer requisição apareceria aqui

    backup.run_backup(conn, falho, _AFTER_HOUR)  # falha no limiar: "envio" sem canal
    assert pedidos == []  # nenhum HTTP aconteceu
    assert _episodio(conn, "backup:failure")["delivered"] == 0

    dia2 = datetime(2026, 10, 25, 8, 0, 0, tzinfo=UTC)
    backup.run_backup(conn, bom, dia2)  # sucesso: transição de recovery sem canal
    assert pedidos == []
    assert _episodio(conn, "backup:failure") is None
    assert _episodio(conn, "backup:recovery")["delivered"] == 0
