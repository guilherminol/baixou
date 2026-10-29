"""Redefinição de senha por comando dentro do container (AUTH-03, SC-3).

Uso (documentado no README)::

    docker compose exec web python -m baixou.web.reset_password
    docker compose exec web python -m baixou.web.reset_password --username admin

Saída de emergência do administrador sem acesso à sessão: troca a senha e
encerra TODAS as sessões do usuário na MESMA transação (T-04-12) — depois do
reset, nenhum cookie antigo autentica. A senha nova é pedida DUAS vezes por
``getpass`` (sem eco; nunca em ``sys.argv`` — não vaza para o histórico do
shell) e nunca aparece em log nem mensagem (K2). Boot no molde do worker:
config → cheque do SQLite → migrações → banco.

RED (04-04): o fluxo real entra no commit GREEN — este esqueleto existe para
os testes serem executáveis e falharem na asserção do comportamento planejado.
Stdlib puro: nenhum import da camada de framework web (o teste de fronteira
segue verde).
"""

from __future__ import annotations


def main() -> None:
    """Fluxo do reset — implementado no GREEN do plano 04-04 (TDD)."""


if __name__ == "__main__":
    main()
