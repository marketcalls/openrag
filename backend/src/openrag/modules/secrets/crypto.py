"""AES-256-GCM primitives backed by an out-of-database key file."""

import base64
import hashlib
import os
import secrets as secure_random
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from openrag.core.errors import SecretsError

KEY_VERSION = 1
_KEK_BYTES = 32
_NONCE_BYTES = 12


def ensure_kek(path: str) -> None:
    key_path = Path(path)
    if key_path.exists():
        return
    key_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        key_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    with os.fdopen(descriptor, "wb") as key_file:
        key_file.write(
            base64.urlsafe_b64encode(secure_random.token_bytes(_KEK_BYTES))
        )


def load_kek(path: str) -> bytes:
    key_path = Path(path)
    if not key_path.exists():
        raise SecretsError(
            "KEK file missing; run `python -m openrag.bootstrap` first"
        )
    try:
        key = base64.urlsafe_b64decode(key_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise SecretsError("KEK file could not be read") from exc
    if len(key) != _KEK_BYTES:
        raise SecretsError("KEK file corrupt: expected 32 key bytes")
    return key


def encrypt(key: bytes, plaintext: str) -> tuple[bytes, bytes]:
    nonce = secure_random.token_bytes(_NONCE_BYTES)
    return nonce, AESGCM(key).encrypt(nonce, plaintext.encode(), None)


def decrypt(key: bytes, nonce: bytes, ciphertext: bytes) -> str:
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None).decode()
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise SecretsError(
            "secret decryption failed (wrong or rotated KEK)"
        ) from exc


def fingerprint(value: str) -> str:
    digest = hashlib.sha256(value.encode()).hexdigest()[:12]
    return f"...{value[-4:]} sha256:{digest}"
