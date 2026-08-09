"""Browser Session cookie, CSRF, and Origin security primitives.

The raw Session credential only exists in an HttpOnly cookie.  A separate
non-HttpOnly random value is double-submitted in ``X-CSRF-Token`` and is also
hashed into the durable Session row.  Extension iframes use distinct CHIPS
cookies so the primary Web Session is never copied into extension storage.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, Response, status

from vibecanvas_api.auth.tokens import hash_token
from vibecanvas_api.config import config
from vibecanvas_api.security_profile import configured_cors_origins

CSRF_HEADER = "X-CSRF-Token"
SESSION_AUDIENCES = frozenset({"web", "extension", "support"})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class CookieCredential:
    audience: str
    raw_session: str
    raw_csrf: str | None


def _secure_cookie_transport() -> bool:
    # Production is never downgradeable. Development may explicitly choose
    # HTTP-compatible cookies while still retaining an HTTPS public callback
    # URL (for example when the same stack is reached through localhost/IP and
    # a workspace proxy). If unset, preserve the canonical-URL auto behavior.
    if config.environment == "production":
        return True
    configured = getattr(config, "web_session_cookie_secure", None)
    if configured is not None:
        return bool(configured)
    try:
        public_scheme = urlsplit(config.public_urls.public_url).scheme
    except ValueError:
        public_scheme = ""
    return public_scheme == "https"


def _cookie_names(audience: str, *, secure: bool | None = None) -> tuple[str, str]:
    if audience not in SESSION_AUDIENCES:
        raise ValueError("unsupported browser Session audience")
    use_secure = _secure_cookie_transport() if secure is None else secure
    namespace = audience
    prefix = "__Host-" if use_secure else ""
    return (
        f"{prefix}vibecanvas-{namespace}-session",
        f"{prefix}vibecanvas-{namespace}-csrf",
    )


def cookie_credential(request: Request) -> CookieCredential | None:
    """Read only the cookie namespace valid for this deployment transport."""
    # Extension first: within a partitioned iframe both cookie families could
    # theoretically exist, but only the extension derivative belongs there.
    secure = _secure_cookie_transport()
    for audience in ("support", "extension", "web"):
        # CHIPS requires ``Secure`` together with ``SameSite=None``. Chrome
        # treats localhost as a trustworthy development origin, so the same
        # secure, partitioned extension cookie works for both local side-panel
        # development and HTTPS deployments. Looking up the insecure extension
        # namespace here would create a login that appears to succeed but whose
        # cookie Chrome immediately rejects.
        audience_secure = True if audience == "extension" else secure
        session_name, csrf_name = _cookie_names(
            audience,
            secure=audience_secure,
        )
        raw_session = request.cookies.get(session_name)
        if raw_session:
            return CookieCredential(
                audience=audience,
                raw_session=raw_session,
                raw_csrf=request.cookies.get(csrf_name),
            )
    return None


def _append_partitioned(response: Response) -> None:
    """Add CHIPS' Partitioned attribute on Python/Starlette versions < 3.14."""
    for index in range(len(response.raw_headers) - 1, -1, -1):
        name, value = response.raw_headers[index]
        if name.lower() == b"set-cookie":
            if b"partitioned" not in value.lower():
                response.raw_headers[index] = (name, value + b"; Partitioned")
            return


def set_session_cookies(
    response: Response,
    *,
    audience: str,
    raw_session: str,
    raw_csrf: str,
    max_age: int,
) -> None:
    # Extension sessions always live in a third-party iframe partition. Modern
    # Chrome only accepts that cookie shape with Secure + SameSite=None +
    # Partitioned; allowing an insecure variant produces a broken post-login
    # /auth/me request and weakens the intended Session boundary.
    secure = True if audience == "extension" else _secure_cookie_transport()
    session_name, csrf_name = _cookie_names(audience, secure=secure)
    same_site = "none" if audience == "extension" else "lax"
    response.set_cookie(
        session_name,
        raw_session,
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=True,
        samesite=same_site,
    )
    if audience == "extension":
        _append_partitioned(response)
    response.set_cookie(
        csrf_name,
        raw_csrf,
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=False,
        samesite=same_site,
    )
    if audience == "extension":
        _append_partitioned(response)
    response.headers["Cache-Control"] = "no-store"


def clear_session_cookie(response: Response, *, audience: str) -> None:
    """Clear one browser Session namespace without touching its parent."""
    if audience not in SESSION_AUDIENCES:
        raise ValueError("unsupported browser Session audience")
    for secure in (True, False):
        session_name, csrf_name = _cookie_names(audience, secure=secure)
        same_site = "none" if audience == "extension" else "lax"
        response.delete_cookie(
            session_name,
            path="/",
            secure=secure,
            httponly=True,
            samesite=same_site,
        )
        if audience == "extension" and secure:
            _append_partitioned(response)
        response.delete_cookie(
            csrf_name,
            path="/",
            secure=secure,
            httponly=False,
            samesite=same_site,
        )
        if audience == "extension" and secure:
            _append_partitioned(response)
    response.headers["Cache-Control"] = "no-store"


def clear_session_cookies(response: Response) -> None:
    # Clear every possible deployment/audience name. This also handles a
    # deployment switching from local HTTP to the secure __Host- namespace.
    for audience in ("web", "extension", "support"):
        clear_session_cookie(response, audience=audience)


def _origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def _allowed_origins(request: Request) -> frozenset[str]:
    candidates = list(configured_cors_origins())
    # A request Host is useful for local TestClient and ad-hoc development,
    # but it is not a production trust anchor: reverse-proxy mistakes and DNS
    # rebinding can make it attacker-controlled. Production accepts only the
    # deployment-owned public URL and explicit CORS allowlist.
    if config.environment != "production":
        candidates.append(str(request.base_url))
    if config.public_urls.public_url:
        candidates.append(config.public_urls.public_url)
    return frozenset(
        normalized for value in candidates if (normalized := _origin(value)) is not None
    )


def validate_browser_origin(request: Request) -> None:
    """Reject unsafe browser requests not issued by an approved app origin."""
    if request.method.upper() not in _UNSAFE_METHODS:
        return
    origin = _origin(request.headers.get("origin", ""))
    if origin is None or origin not in _allowed_origins(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "invalid_request_origin"},
        )


def validate_cookie_request(
    request: Request,
    *,
    credential: CookieCredential,
    stored_csrf_hash: str | None,
) -> None:
    """Require same-session CSRF proof and an approved browser Origin."""
    if request.method.upper() not in _UNSAFE_METHODS:
        return
    validate_browser_origin(request)
    header_csrf = request.headers.get(CSRF_HEADER, "")
    cookie_csrf = credential.raw_csrf or ""
    if (
        not header_csrf
        or not cookie_csrf
        or not hmac.compare_digest(header_csrf, cookie_csrf)
        or not stored_csrf_hash
        or not hmac.compare_digest(hash_token(header_csrf), stored_csrf_hash)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "csrf_validation_failed"},
        )
