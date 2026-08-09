"""Deployments — secret generation. Plaintext returned exactly once at create.

Used by:
* ``routes/deployments.py::create_deployment`` (T4) — generates the api_key
  / hmac_secret at deployment-create time and returns the plaintext in the
  response body (caller MUST persist it; subsequent GETs only return the
  hash / a redacted form per T5).
* ``routes/deployments.py::rotate_api_key`` (T5) — re-uses
  ``generate_api_key`` to mint a fresh key.

Spec §6.1 hard invariant: the plaintext api_key is shown ONCE. The DB
stores only the SHA-256 hex; the resolver
(``resolve_deployment_and_bind_tenant``) re-hashes the inbound bearer
token and looks up by hash. Webhook HMAC material is stored exclusively via
SecretService and is resolved only inside the host verifier boundary.
"""
from __future__ import annotations

import hashlib
import secrets


def generate_api_key() -> tuple[str, str]:
    """Return ``(plaintext, sha256_hex)``.

    Caller stores the hex (``deployments.api_key_hash``) and returns the
    plaintext to the user EXACTLY once in the create response.

    Plaintext shape: ``vc_`` + 43 url-safe base64 chars (~256 bits of
    entropy). The ``vc_`` prefix lets log scrubbers + secret-scanners
    (e.g. GitHub secret scanning custom patterns) match unambiguously.
    """
    plaintext = "vc_" + secrets.token_urlsafe(32)
    return plaintext, hashlib.sha256(plaintext.encode()).hexdigest()


def generate_hmac_secret() -> str:
    """Return a plaintext HMAC secret for webhook signature verification.

    Stored through SecretService and returned to the user EXACTLY once in the
    create response.

    Shape: ``whsec_`` + 64 url-safe base64 chars (~384 bits of entropy).
    The ``whsec_`` prefix mirrors Stripe's webhook-secret convention so
    secret-scanners and operators can identify the credential at a
    glance.
    """
    return "whsec_" + secrets.token_urlsafe(48)
