"""UX-10e — HMAC signing for the VFS signed-URL raw-bytes media endpoint.

The ``GET /api/v1/vfs/raw`` endpoint serves raw file BYTES (images/video/audio)
so an ``<img src>`` / ``<video src>`` can point at a VFS file WITHOUT carrying
an Authorization header (browsers don't attach a Bearer to a media element's
request). Trust comes from a short-lived signature over the URL params instead
of a session token.

Signed payload (the canonical string that the HMAC covers) binds EVERY
authorization-relevant param, including the TENANT — so the tenant the read is
scoped to comes FROM the signature, never a query param the client can forge:

    tenant=<t>&path=<p>&wf_id=<w>&run_id=<r>&exp=<unix_ts>

``sign_vfs_url`` returns those params + ``sig=HMAC_SHA256(secret, canonical)``
(hex). ``verify_vfs_sig`` recomputes the HMAC over the SAME canonical string
built from the inbound params and constant-time-compares; it also rejects an
expired ``exp``. A tampered tenant/path/wf_id/run_id/exp changes the canonical
string → the recomputed HMAC won't match → rejected.

The secret is ``config.signing_secret`` (env ``VIBECANVAS_SIGNING_SECRET`` in
prod; a per-process random fallback in dev/tests).
"""
from __future__ import annotations

import hmac
import base64
import json
import posixpath
import secrets
import time
from hashlib import sha256
from collections.abc import Iterable
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken

from vibecanvas_api.config import config


def _canonical(*, tenant: str, path: str, wf_id: str, run_id: str, exp: int) -> str:
    """Build the exact byte string the HMAC covers.

    Order is fixed and every field is included; `None`/empty are normalized to
    "" so signing and verifying agree on absent optional params (wf_id/run_id).
    """
    return (
        f"tenant={tenant or ''}"
        f"&path={path or ''}"
        f"&wf_id={wf_id or ''}"
        f"&run_id={run_id or ''}"
        f"&exp={int(exp)}"
    )


def _compute_sig(canonical: str) -> str:
    return hmac.new(
        config.signing_secret.encode(), canonical.encode(), sha256
    ).hexdigest()


def _resource_fernet() -> Fernet:
    """Build an opaque, cross-worker capability codec from the shared secret."""
    key = base64.urlsafe_b64encode(sha256(config.signing_secret.encode()).digest())
    return Fernet(key)


def issue_vfs_resource_capability(
    *,
    tenant_id: str,
    audience: str,
    allowed_paths: Iterable[str],
    wf_id: str = "",
    run_id: str = "",
    expires_in_s: int = 300,
) -> str:
    """Mint an opaque capability for resources in one already-authorized VFS scope.

    ``wf_id``/``run_id`` are the durable storage scopes selected by the
    authenticated mint route. ``audience`` prevents a capability minted for one
    renderer from being replayed by another, while ``allowed_paths`` limits it
    to exact files or explicitly declared static directories. No server-local
    cache is involved, so any worker sharing the signing secret can serve the
    resource after a reconnect.
    """
    paths = _normalize_resource_paths(allowed_paths)
    if not audience or len(audience) > 64:
        raise ValueError("resource capability audience is required")
    payload = {
        "v": 2,
        "tenant": str(tenant_id),
        "wf_id": wf_id or "",
        "run_id": run_id or "",
        "aud": audience,
        "op": "read",
        "paths": paths,
        "nonce": secrets.token_urlsafe(18),
        "exp": int(time.time()) + int(expires_in_s),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return _resource_fernet().encrypt(raw).decode()


def verify_vfs_resource_capability(
    token: str,
    *,
    now: int | None = None,
) -> dict[str, str | list[str]] | None:
    """Decode an unexpired VFS resource capability, or return ``None``."""
    try:
        raw = _resource_fernet().decrypt(token.encode())
        payload = json.loads(raw)
        current = int(time.time()) if now is None else int(now)
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 2
            or int(payload.get("exp", 0)) < current
            or not isinstance(payload.get("tenant"), str)
            or not isinstance(payload.get("aud"), str)
            or payload.get("op") != "read"
            or not isinstance(payload.get("nonce"), str)
        ):
            return None
        paths = _normalize_resource_paths(payload.get("paths") or ())
        return {
            "tenant": payload["tenant"],
            "wf_id": str(payload.get("wf_id") or ""),
            "run_id": str(payload.get("run_id") or ""),
            "audience": payload["aud"],
            "operation": "read",
            "allowed_paths": paths,
        }
    except (InvalidToken, ValueError, TypeError, json.JSONDecodeError):
        return None


def _normalize_resource_paths(values: Iterable[str]) -> list[str]:
    """Canonicalize exact file and trailing-slash directory capability rules."""
    if isinstance(values, (str, bytes)):
        raise ValueError("resource capability paths must be a collection")
    normalized: list[str] = []
    for raw in values:
        if (
            not isinstance(raw, str)
            or not raw.startswith("/")
            or len(raw) > 2048
            or "\x00" in raw
            or "\\" in raw
            or "?" in raw
            or "#" in raw
            or any(segment in {".", ".."} for segment in raw.split("/"))
        ):
            raise ValueError("invalid resource capability path")
        is_prefix = raw.endswith("/")
        value = posixpath.normpath(raw)
        if not value.startswith("/"):
            raise ValueError("invalid resource capability path")
        if is_prefix and value != "/":
            value += "/"
        normalized.append(value)
    result = sorted(set(normalized))
    if not result or len(result) > 128:
        raise ValueError("resource capability requires 1..128 path rules")
    return result


def vfs_resource_access_allowed(
    scope: dict[str, str | list[str]],
    *,
    audience: str,
    path: str,
) -> bool:
    """Match the request against the token's immutable audience/action/path."""
    rules = scope.get("allowed_paths")
    return bool(
        scope.get("audience") == audience
        and scope.get("operation") == "read"
        and isinstance(rules, list)
        and all(isinstance(rule, str) for rule in rules)
        and any(
            path.startswith(rule) if rule.endswith("/") else path == rule
            for rule in rules
        )
    )


def sign_vfs_url(
    *,
    tenant_id: str,
    path: str,
    wf_id: str = "",
    run_id: str = "",
    expires_in_s: int = 300,
) -> str:
    """Return a relative URL ``/api/v1/vfs/raw?...&sig=...`` whose query params
    are signed (tenant bound). ``tenant_id`` MUST come from the auth context, not
    the client.
    """
    exp = int(time.time()) + int(expires_in_s)
    tenant = str(tenant_id)
    wf_id = wf_id or ""
    run_id = run_id or ""
    sig = _compute_sig(_canonical(
        tenant=tenant, path=path, wf_id=wf_id, run_id=run_id, exp=exp))
    params = {
        "path": path,
        "wf_id": wf_id,
        "run_id": run_id,
        "exp": exp,
        "tenant": tenant,
        "sig": sig,
    }
    return "/api/v1/vfs/raw?" + urlencode(params)


def verify_vfs_sig(
    *,
    tenant: str,
    path: str,
    wf_id: str,
    run_id: str,
    exp: int,
    sig: str,
    now: int | None = None,
) -> str | None:
    """Verify the signature + expiry. Returns the signed ``tenant`` on success
    (the caller scopes the read to it), or ``None`` on any failure: bad/forged
    signature, malformed ``exp``, or expired.

    The returned tenant is the one that was SIGNED — since the HMAC covers it, a
    client cannot swap in another tenant's id without invalidating the signature.
    """
    if not sig:
        return None
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return None
    now = int(time.time()) if now is None else int(now)
    if exp_i < now:
        return None
    expected = _compute_sig(_canonical(
        tenant=tenant or "", path=path or "", wf_id=wf_id or "",
        run_id=run_id or "", exp=exp_i))
    if not hmac.compare_digest(expected, str(sig)):
        return None
    return str(tenant)
