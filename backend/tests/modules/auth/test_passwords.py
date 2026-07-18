from openrag.modules.auth.passwords import hash_password, verify_password


def test_hash_and_verify() -> None:
    hashed = hash_password("s3cret!")
    assert hashed != "s3cret!"
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "s3cret!")
    assert not verify_password(hashed, "wrong")
