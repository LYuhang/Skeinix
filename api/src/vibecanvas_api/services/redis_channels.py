"""Organization/resource-scoped Redis channel names and event envelopes."""

from __future__ import annotations

import uuid

from vibecanvas_api.config import config
from vibecanvas_api.services.agent_runtime.model_capability import (
    authorization_model_generation,
)


def current_authorization_generation() -> str:
    return authorization_model_generation(
        model_id=config.openfga_authorization_model_id,
    )


def task_event_channel(
    organization_id: str | uuid.UUID,
    task_id: str | uuid.UUID,
    *,
    authorization_generation: str | None = None,
) -> str:
    organization = uuid.UUID(str(organization_id))
    task = uuid.UUID(str(task_id))
    generation = (
        authorization_generation or current_authorization_generation()
    )
    if not generation or any(ch not in "0123456789abcdef" for ch in generation):
        raise ValueError("invalid authorization generation")
    return (
        f"vibecanvas:v1:{generation}:organization:{organization}:"
        f"task:{task}:events"
    )


def task_event_envelope(
    *,
    organization_id: str | uuid.UUID,
    task_id: str | uuid.UUID,
    event: dict,
    authorization_generation: str | None = None,
) -> dict:
    generation = (
        authorization_generation or current_authorization_generation()
    )
    return {
        **event,
        "organization_id": str(uuid.UUID(str(organization_id))),
        "resource_type": "task",
        "resource_id": str(uuid.UUID(str(task_id))),
        "authorization_generation": generation,
    }


def task_event_envelope_matches(
    event: object,
    *,
    organization_id: str | uuid.UUID,
    task_id: str | uuid.UUID,
    authorization_generation: str | None = None,
) -> bool:
    if not isinstance(event, dict):
        return False
    generation = (
        authorization_generation or current_authorization_generation()
    )
    return (
        event.get("organization_id") == str(uuid.UUID(str(organization_id)))
        and event.get("resource_type") == "task"
        and event.get("resource_id") == str(uuid.UUID(str(task_id)))
        and event.get("authorization_generation") == generation
    )


__all__ = [
    "current_authorization_generation",
    "task_event_channel",
    "task_event_envelope",
    "task_event_envelope_matches",
]
