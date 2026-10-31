"""Invariante de empacotamento da wheel (WR-01 da 01-REVIEW, plano 06-01).

A descoberta de migrações por ``default_migrations_dir()`` só funciona em uma
instalação não-editável (``uv sync --no-editable`` da imagem) se o diretório
de migrações vive DENTRO do pacote ``baixou`` — na wheel, o caminho vira
``site-packages/baixou/migrations`` e o MESMO cálculo ``parents[1]`` do repositório
continua válido. Estes testes provam a co-localização sem rede e sem Docker:
o inspection da wheel em si é feito no <verify> do plano (``uv build`` + zipfile).
"""

from __future__ import annotations

from baixou.core.migrations import _discover, default_migrations_dir


def test_default_migrations_dir_is_inside_the_baixou_package():
    """Co-localização: o diretório default fica DENTRO do pacote baixou.

    No repositório é ``backend/baixou/migrations``; na wheel instalada
    não-editável, ``site-packages/baixou/migrations``. Em ambos,
    ``default_migrations_dir().parent`` é o próprio pacote ``baixou`` —
    é isso que garante que a instalação não-editável encontra as migrações.
    """
    migrations_dir = default_migrations_dir()

    assert migrations_dir.parent.name == "baixou"
    assert migrations_dir.is_dir(), (
        f"{migrations_dir} não existe — as migrações saíram do pacote "
        "(a descoberta falharia em silêncio numa instalação não-editável)"
    )


def test_default_dir_discovers_exactly_the_four_published_migrations():
    """A descoberta encontra exatamente as versões 0001, 0002, 0003 e 0004.

    Menos que isso quebra bancos novos (esquema incompleto); mais que isso
    indica arquivo de migração perdido no diretório do pacote.
    """
    discovered = _discover(default_migrations_dir())

    assert [version for version, _ in discovered] == ["0001", "0002", "0003", "0004"]
    assert all(path.suffix == ".sql" for _, path in discovered)
