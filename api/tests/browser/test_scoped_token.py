from vibecanvas_api.browser.scoped_token import (
    BROWSER_TOKEN_AUDIENCE,
    MAX_BROWSER_TOKEN_TTL_S,
    ScopedAuth,
    mint_scoped_token,
    verify_scoped_token,
)

SECRET = "test-secret-aaaa"


def _mint(*, ttl_s=MAX_BROWSER_TOKEN_TTL_S, now=1000):
    return mint_scoped_token(
        "u1",
        "t1",
        "wf1",
        SECRET,
        browser_id="b1",
        extension_id="a" * 32,
        session_id="s1",
        session_generation=3,
        session_audience="extension",
        ttl_s=ttl_s,
        now=now,
    )


def test_roundtrip_valid_token():
    tok = _mint()
    auth = verify_scoped_token(tok, SECRET, now=1500)
    assert auth == ScopedAuth(
        user_id="u1",
        tenant_id="t1",
        wf_id="wf1",
        browser_id="b1",
        extension_id="a" * 32,
        session_id="s1",
        session_generation=3,
        session_audience="extension",
        iat=1000,
        exp=1900,
        audience=BROWSER_TOKEN_AUDIENCE,
    )


def test_expired_token_rejected():
    tok = _mint(ttl_s=10)
    assert verify_scoped_token(tok, SECRET, now=2000) is None


def test_tampered_payload_rejected():
    # NOTE (deviation from plan): the plan forged via body.replace("t1","t2"),
    # but the payload is base64url-encoded so the literal "t1" never appears in
    # `body` — the replace is a no-op and the "forged" token equals the original,
    # so the original test asserted nothing. We instead mutate the encoded body
    # directly (flip its first char) so the signature genuinely no longer matches,
    # preserving the intent: any tamper to the signed body must be rejected.
    tok = _mint()
    body, sig = tok.rsplit(".", 1)
    flipped = ("B" if body[0] != "B" else "C") + body[1:]
    forged = flipped + "." + sig
    assert forged != tok
    assert verify_scoped_token(forged, SECRET, now=1000) is None


def test_wrong_secret_rejected():
    tok = _mint()
    assert verify_scoped_token(tok, "other-secret", now=1000) is None


def test_legacy_unbound_token_shape_is_rejected():
    import base64
    import hashlib
    import hmac
    import json

    body = (
        base64.urlsafe_b64encode(
            json.dumps({"u": "u1", "t": "t1", "w": "wf1", "exp": 1900}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    signature = (
        base64.urlsafe_b64encode(
            hmac.new(SECRET.encode(), body.encode(), hashlib.sha256).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert verify_scoped_token(f"{body}.{signature}", SECRET, now=1000) is None


def test_ttl_cannot_exceed_short_lived_boundary():
    import pytest

    with pytest.raises(ValueError):
        _mint(ttl_s=MAX_BROWSER_TOKEN_TTL_S + 1)
