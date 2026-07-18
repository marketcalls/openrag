import re
import stat
from pathlib import Path

import pytest

from openrag.core.errors import SecretsError
from openrag.modules.secrets.crypto import (
    decrypt,
    encrypt,
    ensure_kek,
    fingerprint,
    load_kek,
)


def test_ensure_kek_creates_0600_and_is_idempotent(tmp_path: Path) -> None:
    path = str(tmp_path / "kek")

    ensure_kek(path)

    assert stat.S_IMODE(Path(path).stat().st_mode) == 0o600
    first = Path(path).read_bytes()
    ensure_kek(path)
    assert Path(path).read_bytes() == first
    assert len(load_kek(path)) == 32


def test_load_missing_kek_raises(tmp_path: Path) -> None:
    with pytest.raises(SecretsError):
        load_kek(str(tmp_path / "missing"))


def test_encrypt_decrypt_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "kek")
    ensure_kek(path)
    key = load_kek(path)

    nonce, ciphertext = encrypt(key, "sk-super-secret-value")

    assert b"sk-super-secret-value" not in ciphertext
    assert decrypt(key, nonce, ciphertext) == "sk-super-secret-value"


def test_wrong_kek_fails_closed(tmp_path: Path) -> None:
    first = str(tmp_path / "first")
    second = str(tmp_path / "second")
    ensure_kek(first)
    ensure_kek(second)
    nonce, ciphertext = encrypt(load_kek(first), "value")

    with pytest.raises(SecretsError):
        decrypt(load_kek(second), nonce, ciphertext)


def test_fingerprint_format_does_not_leak_value() -> None:
    fingerprint_value = fingerprint("sk-abcdef1234567890wxyz")

    assert re.fullmatch(
        r"\.\.\.wxyz sha256:[0-9a-f]{12}",
        fingerprint_value,
    )
    assert "sk-abcdef1234567890" not in fingerprint_value
