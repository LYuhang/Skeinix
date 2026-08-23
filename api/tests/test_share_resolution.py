"""Short-lived share-target tokens bind every security-sensitive claim."""

from __future__ import annotations

import pytest

from vibecanvas_api.authorization.share_resolution import (
    ShareResolution,
    binding_from_share_resolution,
    mint_share_resolution,
)
from vibecanvas_api.authorization.types import ResourceRef, ResourceType


def _token() -> str:
    return mint_share_resolution(ShareResolution(
        actor_user_id="actor",
        session_id="session",
        owner_organization_id="owner",
        resource_type="workflow",
        resource_id="workflow-1",
        subject_type="user",
        subject_id="recipient",
        subject_relation=None,
        allowed_relations=("viewer", "editor"),
    ))


def test_resolution_builds_only_an_allowed_exact_resource_binding():
    binding = binding_from_share_resolution(
        _token(),
        relation="editor",
        actor_user_id="actor",
        session_id="session",
        resource=ResourceRef(ResourceType.WORKFLOW, "workflow-1", "owner"),
    )

    assert binding.relation == "editor"
    assert binding.subject.id == "recipient"
    assert binding.resource.id == "workflow-1"


@pytest.mark.parametrize(
    ("relation", "actor", "session", "resource_id"),
    [
        ("manager", "actor", "session", "workflow-1"),
        ("viewer", "other", "session", "workflow-1"),
        ("viewer", "actor", "other", "workflow-1"),
        ("viewer", "actor", "session", "workflow-2"),
    ],
)
def test_resolution_rejects_role_or_claim_substitution(
    relation: str,
    actor: str,
    session: str,
    resource_id: str,
):
    with pytest.raises(ValueError):
        binding_from_share_resolution(
            _token(),
            relation=relation,
            actor_user_id=actor,
            session_id=session,
            resource=ResourceRef(
                ResourceType.WORKFLOW,
                resource_id,
                "owner",
            ),
        )


def test_resolution_rejects_signature_tampering():
    token = _token()
    body, signature = token.rsplit(".", 1)
    replacement = "A" if signature[-1] != "A" else "B"
    with pytest.raises(ValueError, match="invalid_share_resolution"):
        binding_from_share_resolution(
            f"{body}.{signature[:-1]}{replacement}",
            relation="viewer",
            actor_user_id="actor",
            session_id="session",
            resource=ResourceRef(
                ResourceType.WORKFLOW,
                "workflow-1",
                "owner",
            ),
        )
