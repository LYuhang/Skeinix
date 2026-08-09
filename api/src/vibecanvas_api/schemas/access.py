"""Uniform backend-computed effective access projection."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from vibecanvas_api.authorization.types import Action, Decision
from vibecanvas_api.config import config


class ResourceAccessOut(BaseModel):
    capabilities: list[Action] = Field(default_factory=list)
    effective_role: str | None = None
    source: str = "computed"


class DirectBindingIn(BaseModel):
    relation: Literal["viewer", "editor", "operator", "manager"]
    subject_type: Literal[
        "user",
        "service_account",
        "group",
        "organization",
    ]
    subject_id: str = Field(min_length=1, max_length=512)
    subject_relation: Literal["direct_member", "member"] | None = None

    @model_validator(mode="after")
    def validate_subject_shape(self) -> "DirectBindingIn":
        if self.subject_type == "group" and self.subject_relation is None:
            raise ValueError("group binding requires a membership relation")
        if self.subject_type == "organization":
            if self.subject_relation != "member" or self.relation != "viewer":
                raise ValueError(
                    "organization-wide binding supports viewer#member only"
                )
        if self.subject_type in {"user", "service_account"}:
            if self.subject_relation is not None:
                raise ValueError("direct principal binding cannot have a relation")
        if self.subject_type == "service_account" and self.relation != "operator":
            raise ValueError("service account binding supports operator only")
        return self


class DirectBindingOut(DirectBindingIn):
    source: Literal["direct"] = "direct"


class DirectBindingListOut(BaseModel):
    items: list[DirectBindingOut] = Field(default_factory=list)
    continuation_token: str = ""


def access_from_decision(
    decision: Decision,
    *,
    source: str = "computed",
) -> ResourceAccessOut:
    capabilities = decision.capabilities
    if not config.resource_sharing_enabled:
        capabilities = frozenset(
            action
            for action in capabilities
            if action != Action.MANAGE_ACCESS
        )
    return ResourceAccessOut(
        capabilities=sorted(capabilities, key=str),
        effective_role=decision.effective_role,
        source=source,
    )


def decision_allows_content(decision: Decision) -> bool:
    """Return whether a response may include user-authored content.

    Organization administrators and auditors deliberately receive
    ``view_metadata`` without ``view``. Keeping this check next to the shared
    access projection prevents an allowed inventory decision from being
    mistaken for content authorization by individual list serializers.
    """
    return Action.VIEW in decision.capabilities
