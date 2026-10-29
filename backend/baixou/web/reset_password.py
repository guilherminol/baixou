"""Redefinição de senha por comando dentro do container (AUTH-03, SC-3).

Uso (documentado no README)::

    docker compose exec web python -m baixou.web.reset_password
    docker compose exec web python -m baixou.web.reset_password --username admin

Saída de emergência do administrador sem acesso à sessão: troca a senha e
encerra TODAS as sessões do usuário na MESMA transação (T-04-12) — depois do
reset, nenhum cookie antigo autentica e o login só passa com a senha nova. A
senha nova é pedida DUAS vezes por ``getpass`` (sem eco; nunca em
``sys.argv`` — não vaza para o histórico do shell) e nunca aparece em log,
mensagem ou erro (K2). Boot no molde do worker: config → cheque do SQLite →
migrações → banco. Stdlib puro: nenhum import da camada de framework web — o
teste de fronteira (TID251) segue verde.
"""

from __future__ import annotations

import argparse
import getpass

from baixou.core.config import Settings, load_settings
from baixou.core.db import WORKER_TIMEOUT_S, check_sqlite_version, connect, write_tx
from baixou.core.log import configure, log
from baixou.core.migrations import apply_migrations
from baixou.web.passwords import hash_password

_MIN_PASSWORD_CHARS = 12


def _build_parser() -> argparse.ArgumentParser:
    """``--username`` é o ÚNICO argumento: senha por flag NUNCA (K2)."""
    parser = argparse.ArgumentParser(
        prog="python -m baixou.web.reset_password",
        description="Redefine a senha do administrador e encerra todas as sessões dele.",
    )
    parser.add_argument(
        "--username",
        default="admin",
        help="nome do usuário a redefinir (padrão: admin)",
    )
    return parser


def main() -> None:
    """Boot no molde do worker e fluxo do reset (senha e sessões na mesma tx)."""
    args = _build_parser().parse_args()
    settings: Settings = load_settings()
    configure(settings.log_level)
    check_sqlite_version()
    apply_migrations(settings.db_path)

    conn = connect(settings.db_path, timeout_s=WORKER_TIMEOUT_S)
    try:
        user = conn.execute(
            "SELECT id, username FROM users WHERE username = ?", (args.username,)
        ).fetchone()
        if user is None:
            # Erro limpo ANTES de pedir qualquer senha: a write_tx abaixo só
            # roda para o user_id encontrado — 0 linhas não cria usuário nem
            # apaga sessão de ninguém (borda da sonda, AUTH-03).
            print(f"usuário não encontrado: {args.username}")
            raise SystemExit(1)

        senha = getpass.getpass("Nova senha: ")
        if senha != getpass.getpass("Confirme a nova senha: "):
            print("as senhas não coincidem")
            raise SystemExit(1)
        if len(senha) < _MIN_PASSWORD_CHARS:
            print("a senha deve ter pelo menos 12 caracteres")
            raise SystemExit(1)

        # Hashing FORA da transação (16 MiB por hashing — mesma disciplina do
        # login): a write_tx é curta, só o UPDATE e o DELETE.
        password_hash = hash_password(senha)
        with write_tx(conn):
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash, user["id"]),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
    finally:
        conn.close()

    # Sem senha, sem hash, sem username no log (K2).
    log("info", "web", "password_reset", text="senha redefinida e sessões encerradas")


if __name__ == "__main__":
    main()
