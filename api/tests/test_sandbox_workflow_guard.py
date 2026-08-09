"""Credential-free sandbox workflow admission and file-channel routing."""
from __future__ import annotations

import json
import os

import pytest

import vibecanvas_api.sandbox_entry as entry
from vibecanvas_api.services.sandbox import (
    EngineNeedsHostNode,
    SANDBOX_RUNNABLE_NODE_TYPES,
    classify_workflow,
)
from vibecanvas_engine.nodes import ENGINE_PURE_NODE_TYPES
from vibecanvas_engine.register import node_registry


def _write_job(work_dir: str, job_id: str, payload: dict) -> None:
    inbox = os.path.join(work_dir, "inbox")
    os.makedirs(inbox, exist_ok=True)
    with open(
        os.path.join(inbox, f"{job_id}.json"),
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(payload, handle)
    with open(
        os.path.join(inbox, f"{job_id}.ready"),
        "w",
        encoding="utf-8",
    ):
        pass


def test_api_entrypoint_does_not_register_host_data_nodes():
    with pytest.raises(KeyError):
        node_registry.get("KnowledgeSearchNode")


def test_run_one_uses_tenant_only_as_structural_metadata(monkeypatch):
    seen = {}

    def fake_run_job(run_root, run_id):
        seen.update(run_root=run_root, run_id=run_id)
        return 0

    monkeypatch.setattr(entry, "run_job", fake_run_job)
    assert entry.run_one("/run", "run-abc", "organization-a") == 0
    assert seen == {"run_root": "/run", "run_id": "run-abc"}


def test_serve_path_uses_bounded_run_subpath(monkeypatch, tmp_path):
    work = str(tmp_path / "work")
    runs = str(tmp_path / "runs")
    seen = {}

    def fake_run_job(run_root, run_id):
        seen.update(run_root=run_root, run_id=run_id)
        return 0

    monkeypatch.setattr(entry, "run_job", fake_run_job)
    _write_job(
        work,
        "job-1",
        {
            "tenant": "organization-a",
            "run_id": "run-1",
            "run_subpath": "run-1",
        },
    )
    assert entry.serve_once_api(work, runs) == "job-1"
    assert seen == {
        "run_root": os.path.join(runs, "run-1"),
        "run_id": "run-1",
    }


def test_serve_path_rejects_escaping_run_subpath(monkeypatch, tmp_path):
    work = str(tmp_path / "work")
    runs = str(tmp_path / "runs")
    calls = []
    monkeypatch.setattr(
        entry,
        "run_job",
        lambda *args: calls.append(args),
    )
    _write_job(
        work,
        "job-escape",
        {
            "tenant": "organization-a",
            "run_id": "run-escape",
            "run_subpath": "../escape",
        },
    )
    assert entry.serve_once_api(work, runs) in {"job-escape", None}
    assert calls == []
    assert os.path.exists(
        os.path.join(work, "outbox", "job-escape.done")
    )


def test_workflow_guard_has_no_database_capability_class():
    assert SANDBOX_RUNNABLE_NODE_TYPES == frozenset(ENGINE_PURE_NODE_TYPES)
    workflow = {
        "node_1": {"node_type": "StartNode"},
        "node_2": {"node_type": "EndNode"},
        "__meta__": {"imported": True},
    }
    assert classify_workflow(workflow) == "pure"


@pytest.mark.parametrize(
    "node_type",
    ["KnowledgeSearchNode", "SomeUnvettedHostNode"],
)
def test_host_data_node_requires_broker(node_type):
    with pytest.raises(EngineNeedsHostNode, match=node_type):
        classify_workflow({"node": {"node_type": node_type}})
