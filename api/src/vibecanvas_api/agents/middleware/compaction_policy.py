"""Per-content_type compaction policy registry (context-lifecycle middleware).

aged_form in {'reference', 'minimal', 'head_tail'}. Workflow:// projections are
handled by a separate marker path in LifecyclePolicyEdit (NOT here) — this
registry keys standard envelope outputs by their output.content_type.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    fresh_k: int       # most-recent N outputs of this type stay FULL
    priority: int      # higher = larger fresh window / kept longer
    aged_form: str     # 'reference' | 'minimal' | 'head_tail'


DEFAULT_POLICY = CompactionPolicy(fresh_k=2, priority=50, aged_form="reference")

_REGISTRY: dict[str, CompactionPolicy] = {
    "text/plain": CompactionPolicy(fresh_k=4, priority=80, aged_form="reference"),
    "text/html": CompactionPolicy(fresh_k=4, priority=80, aged_form="reference"),
    "text/markdown": CompactionPolicy(fresh_k=4, priority=80, aged_form="reference"),
    "table/jsonl": CompactionPolicy(fresh_k=3, priority=50, aged_form="reference"),
    "table/csv": CompactionPolicy(fresh_k=3, priority=50, aged_form="reference"),
    "table/tsv": CompactionPolicy(fresh_k=3, priority=50, aged_form="reference"),
    "application/json": CompactionPolicy(fresh_k=3, priority=50, aged_form="reference"),
    "text/python": CompactionPolicy(fresh_k=3, priority=50, aged_form="reference"),
    "text/shell": CompactionPolicy(fresh_k=1, priority=20, aged_form="head_tail"),
    "link/cloud_table": CompactionPolicy(fresh_k=2, priority=20, aged_form="reference"),
}


def policy_for(content_type: str | None) -> CompactionPolicy:
    if not content_type:
        return DEFAULT_POLICY
    return _REGISTRY.get(content_type.lower(), DEFAULT_POLICY)
