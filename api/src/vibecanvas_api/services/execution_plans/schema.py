"""Strict authoring contract for Dynamic Execution Plan revision 1."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class StartNode(_StrictModel):
    id: str
    type: Literal["start"]
    title: str | None = Field(default=None, max_length=160)
    next: list[str] = Field(min_length=1, max_length=12)


class EndNode(_StrictModel):
    id: str
    type: Literal["end"]
    title: str | None = Field(default=None, max_length=160)


class SubagentNode(_StrictModel):
    id: str
    type: Literal["subagent"]
    title: str = Field(min_length=1, max_length=160)
    task: str = Field(min_length=1, max_length=12_000)
    next: list[str] = Field(min_length=1, max_length=12)


ExecutionNode = Annotated[
    StartNode | SubagentNode | EndNode,
    Field(discriminator="type"),
]


class PlanBudgets(_StrictModel):
    max_wall_time_seconds: int = Field(default=1800, ge=30, le=7200)


class ExecutionPlanV1(_StrictModel):
    schema_version: Literal[1]
    title: str = Field(min_length=1, max_length=200)
    nodes: list[ExecutionNode] = Field(min_length=2, max_length=30)
    budgets: PlanBudgets = Field(default_factory=PlanBudgets)
