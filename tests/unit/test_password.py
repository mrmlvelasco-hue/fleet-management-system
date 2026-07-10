from app.core.security.password import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("s3cret!")
    assert h != "s3cret!"
    assert verify_password(h, "s3cret!") is True
    assert verify_password(h, "wrong") is False
