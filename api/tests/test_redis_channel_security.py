from __future__ import annotations

import uuid

import pytest

from vibecanvas_api.services.redis_channels import (
    task_event_channel,
    task_event_envelope,
    task_event_envelope_matches,
)
from vibecanvas_api.services.sse_bridge import task_event_stream


def test_task_channel_binds_organization_resource_and_authz_generation():
    organization_id = uuid.uuid4()
    task_id = uuid.uuid4()
    generation = "a" * 64
    channel = task_event_channel(
        organization_id,
        task_id,
        authorization_generation=generation,
    )
    assert str(organization_id) in channel
    assert str(task_id) in channel
    assert generation in channel
    assert task_event_channel(
        uuid.uuid4(),
        task_id,
        authorization_generation=generation,
    ) != channel
    assert task_event_channel(
        organization_id,
        task_id,
        authorization_generation="b" * 64,
    ) != channel


def test_task_event_envelope_rejects_cross_resource_or_stale_generation():
    organization_id = uuid.uuid4()
    task_id = uuid.uuid4()
    generation = "c" * 64
    event = task_event_envelope(
        organization_id=organization_id,
        task_id=task_id,
        event={"id": 1, "event_type": "progress", "payload": {}},
        authorization_generation=generation,
    )
    assert task_event_envelope_matches(
        event,
        organization_id=organization_id,
        task_id=task_id,
        authorization_generation=generation,
    )
    assert not task_event_envelope_matches(
        event,
        organization_id=organization_id,
        task_id=uuid.uuid4(),
        authorization_generation=generation,
    )
    assert not task_event_envelope_matches(
        event,
        organization_id=organization_id,
        task_id=task_id,
        authorization_generation="d" * 64,
    )


@pytest.mark.asyncio
async def test_task_stream_closes_before_replay_when_lease_is_revoked():
    calls = 0

    async def denied() -> bool:
        nonlocal calls
        calls += 1
        return False

    stream = task_event_stream(
        task_id=uuid.uuid4(),
        last_event_id=0,
        tenant_id=str(uuid.uuid4()),
        redis_url=None,
        authorization_guard=denied,
    )
    with pytest.raises(StopAsyncIteration):
        await anext(stream)
    assert calls == 1
