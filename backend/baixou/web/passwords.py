"""Hash de senha (scrypt) e tokens de sessão — stdlib puro, SEM fastapi/pydantic.

O CLI de reset de senha (plano 04-04) reutiliza este módulo sem puxar a
camada web: nenhum import de framework aqui — só a biblioteca padrão.

scrypt com 128·r·n = 16 MiB exatos (arquitetura §Senha e sessão);
``maxmem`` explícito de 64 MiB porque o teto herdado do OpenSSL é 32 MiB e
estouraria com n=2**14, r=8. O semáforo de módulo limita a DOIS hashings
simultâneos (AUTH-02/T-04-05): cada hashing segura 16 MiB e o servidor de
referência tem 4 GB de RAM divididos com outros serviços. Os parâmetros
(n, r, p, salt) ficam gravados no PRÓPRIO hash, então uma troca futura de
parâmetros continua verificando hashes antigos.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64
_SCRYPT_MAXMEM = 64 * 2**20
_SALT_BYTES = 16
_TOKEN_BYTES = 32

# No máximo 2 hashings simultâneos (AUTH-02): BoundedSemaphore levanta se
# liberar mais que o teto — erro de programação não passa em silêncio.
_SCRYPT_SLOTS = threading.BoundedSemaphore(2)


def hash_password(password: str) -> str:
    """Gera "scrypt$n$r$p$<salt_hex>$<hash_hex>" com sal de 16 bytes aleatório."""
    salt = os.urandom(_SALT_BYTES)
    with _SCRYPT_SLOTS:
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=_SCRYPT_DKLEN,
            maxmem=_SCRYPT_MAXMEM,
        )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verifica contra o formato gravado; falso em qualquer entrada malformada.

    Compara em tempo constante (hmac.compare_digest). Parâmetros vêm do
    próprio hash — hashes antigos continuam válidos após mudança de padrão.
    """
    if not stored:
        return False
    try:
        scheme, raw_n, raw_r, raw_p, salt_hex, hash_hex = stored.split("$")
        expected = bytes.fromhex(hash_hex)
    except ValueError, AttributeError:
        return False
    if scheme != "scrypt":
        return False
    try:
        with _SCRYPT_SLOTS:
            derived = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=int(raw_n),
                r=int(raw_r),
                p=int(raw_p),
                dklen=len(expected),
                maxmem=_SCRYPT_MAXMEM,
            )
    except ValueError:
        return False
    return hmac.compare_digest(derived, expected)


def new_token() -> str:
    """Token de sessão/setup com 256 bits de entropia (T-04-02)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


def token_digest(token: str) -> str:
    """SHA-256 hex do token — o banco guarda SÓ o digest, nunca o token (K2)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
