"""Shared base64url keyset cursor: ``{ts, id}`` JSON, for DESC
``(created_at, id)`` pagination. Lifted verbatim from ``routes/deployments.py``
so deployment-history and audit-list endpoints share
one encode/decode implementation.

The ``(ts, id)`` tuple is exactly the tuple the SQL ``ORDER BY`` uses — encoded
as JSON so we don't have to commit to a delimiter / escape policy. ``id`` is a
uuid in both call sites (deployment task id and audit_id). ``decode_cursor``
raises ``HTTPException(400)`` on any malformed input — an opaque cursor is the
client's responsibility to round-trip verbatim.
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime

from fastapi import HTTPException, status


def encode_cursor(ts: datetime, row_id: uuid.UUID) -> str:
    payload = json.dumps({
        "ts": ts.isoformat(),
        "id": str(row_id),
    }).encode()
    return base64.urlsafe_b64encode(payload).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(payload["ts"]), uuid.UUID(payload["id"])
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid cursor",
        ) from exc
