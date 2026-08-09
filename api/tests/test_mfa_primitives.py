from __future__ import annotations

import re

from vibecanvas_api.auth.mfa import (
    TOTP_PERIOD_SECONDS,
    matching_totp_step,
    new_recovery_codes,
    new_totp_secret,
    normalize_recovery_code,
    provisioning_uri,
    recovery_code_hash,
    totp_code,
)


RFC_6238_SHA1_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_totp_matches_rfc_6238_sha1_vector_and_adjacent_skew() -> None:
    # RFC 6238's 8-digit result at T=59 is 94287082; a 6-digit profile uses
    # the same dynamic truncation and therefore returns its final six digits.
    assert totp_code(RFC_6238_SHA1_SECRET, step=1) == "287082"
    assert matching_totp_step(
        RFC_6238_SHA1_SECRET,
        "287082",
        now=59,
    ) == 1
    assert matching_totp_step(
        RFC_6238_SHA1_SECRET,
        totp_code(RFC_6238_SHA1_SECRET, step=2),
        now=TOTP_PERIOD_SECONDS,
    ) == 2
    assert matching_totp_step(
        RFC_6238_SHA1_SECRET,
        totp_code(RFC_6238_SHA1_SECRET, step=3),
        now=TOTP_PERIOD_SECONDS,
    ) is None


def test_totp_secret_and_provisioning_uri_are_interoperable() -> None:
    secret = new_totp_secret()
    assert len(secret) == 32
    assert re.fullmatch(r"[A-Z2-7]+", secret)
    uri = provisioning_uri(
        secret=secret,
        account_name="person+test@example.com",
        issuer="Skeinix",
    )
    assert uri.startswith("otpauth://totp/Skeinix%3Aperson%2Btest%40example.com?")
    assert f"secret={secret}" in uri
    assert "algorithm=SHA1&digits=6&period=30" in uri


def test_recovery_codes_are_high_entropy_unique_and_domain_bound() -> None:
    codes = new_recovery_codes()
    assert len(codes) == 10
    assert len(set(codes)) == 10
    assert all(re.fullmatch(r"[A-Z2-7]{5}(?:-[A-Z2-7]{5}){4}", code) for code in codes)
    normalized = normalize_recovery_code(codes[0])
    assert len(normalized) == 25
    assert recovery_code_hash(user_id="user-a", code=codes[0]) == recovery_code_hash(
        user_id="user-a",
        code=normalized.lower(),
    )
    assert recovery_code_hash(user_id="user-a", code=codes[0]) != recovery_code_hash(
        user_id="user-b",
        code=codes[0],
    )
