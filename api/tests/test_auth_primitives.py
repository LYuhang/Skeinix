from vibecanvas_api.auth.password import hash_password, verify_password
from vibecanvas_api.auth.tokens import new_token, hash_token


def test_password_roundtrip():
    h = hash_password("hunter2pw")
    assert h != "hunter2pw"               # never plaintext
    assert verify_password("hunter2pw", h) is True
    assert verify_password("wrong", h) is False


def test_password_salted():
    assert hash_password("same") != hash_password("same")  # random salt


def test_token_hash_stable_and_opaque():
    raw, hashed = new_token()
    assert len(raw) >= 32
    assert hashed == hash_token(raw)
    assert hashed != raw
