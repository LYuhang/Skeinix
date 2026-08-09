"""Atomic Session rotation for authentication-strength changes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid

from fastapi import Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.auth.session_security import set_session_cookies
from vibecanvas_api.auth.tokens import new_token
from vibecanvas_api.config import config
from vibecanvas_api.storage.models import Session


@dataclass(frozen=True, slots=True)
class SessionElevation:
    raw_session: str
    raw_csrf: str
    audience: str
    session_id: uuid.UUID
    generation: int
    expires_at: datetime
    authentication_strength: str
    step_up_expires_at: datetime | None

    def publish(self, response: Response) -> dict:
        """Publish cookies only after the surrounding DB transaction commits."""
        if config.web_session_cookie_enabled:
            set_session_cookies(
                response,
                audience=self.audience,
                raw_session=self.raw_session,
                raw_csrf=self.raw_csrf,
                max_age=max(
                    1,
                    int(
                        (
                            self.expires_at - datetime.now(timezone.utc)
                        ).total_seconds()
                    ),
                ),
            )
            return {}
        return {"session_token": self.raw_session}


async def rotate_authentication_strength(
    session: AsyncSession,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    strength: str,
    step_up_ttl: timedelta | None = None,
) -> SessionElevation:
    """Rotate the opaque credential and atomically update its assurance."""
    raw_session, token_hash = new_token()
    raw_csrf, csrf_hash = new_token()
    now = datetime.now(timezone.utc)
    step_up_expires_at = now + step_up_ttl if step_up_ttl is not None else None
    result = (
        await session.execute(
            update(Session)
            .where(
                Session.session_id == session_id,
                Session.user_id == user_id,
                Session.expires_at > now,
            )
            .values(
                token_hash=token_hash,
                csrf_token_hash=csrf_hash,
                authentication_strength=strength,
                step_up_expires_at=step_up_expires_at,
                generation=Session.generation + 1,
                last_seen_at=now,
            )
            .returning(
                Session.audience,
                Session.session_id,
                Session.generation,
                Session.expires_at,
            )
        )
    ).one_or_none()
    if result is None:
        raise LookupError("authenticated Session disappeared during rotation")
    await session.flush()
    return SessionElevation(
        raw_session=raw_session,
        raw_csrf=raw_csrf,
        audience=str(result.audience),
        session_id=result.session_id,
        generation=int(result.generation),
        expires_at=result.expires_at,
        authentication_strength=strength,
        step_up_expires_at=step_up_expires_at,
    )
