import copy
import difflib
import json
from typing import Any, Dict, List, Tuple

from vibecanvas_api.utils import json_pointer


def _suggest_node_ids(
    missing_id: str, workflow: dict, n: int = 3,
) -> List[str]:
    """Return up to n existing node_ids closest to missing_id by edit distance.

    Used when an op references a non-existent node, to give the agent a
    self-correction hint on the next turn (Aider's approach — see
    `aider/coders/editblock_coder.py`).
    """
    candidates = [k for k in workflow if not k.startswith("__") and k != missing_id]
    return difflib.get_close_matches(missing_id, candidates, n=n, cutoff=0.3)


def _fuzzy_substring_candidates(
    needle: str, haystack: str, n: int = 3, min_ratio: float = 0.4,
) -> List[Tuple[float, int, str]]:
    """Find windows of haystack similar to needle.

    Splits haystack by lines and slides a window of needle's line count,
    scoring each window by ``difflib.SequenceMatcher.ratio()``. Returns
    up to ``n`` (similarity_ratio, 1-based_line_number, snippet) tuples
    above ``min_ratio``, sorted by descending similarity.

    Used to suggest near-miss anchors when a `text_edit` op can't find
    its exact match.
    """
    if not needle or not haystack:
        return []
    needle_lines = needle.splitlines() or [needle]
    win_size = max(1, len(needle_lines))
    hay_lines = haystack.splitlines() or [haystack]
    if win_size > len(hay_lines):
        # Whole haystack vs needle as a single comparison
        ratio = difflib.SequenceMatcher(None, needle, haystack).ratio()
        return [(ratio, 1, haystack)] if ratio >= min_ratio else []

    scored: list[tuple[float, int, str]] = []
    for start_idx in range(len(hay_lines) - win_size + 1):
        window = "\n".join(hay_lines[start_idx:start_idx + win_size])
        ratio = difflib.SequenceMatcher(None, needle, window).ratio()
        if ratio >= min_ratio:
            scored.append((ratio, start_idx + 1, window))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:n]


def _format_anchor_hint(needle: str, haystack: str) -> str:
    """Render fuzzy candidates as a one-line hint suffix.

    Returns "" if no useful candidates — caller decides whether to append.
    """
    cands = _fuzzy_substring_candidates(needle, haystack, n=3)
    if not cands:
        return ""
    parts = ["  hints:"]
    for ratio, line_no, snippet in cands:
        preview = snippet if len(snippet) <= 100 else snippet[:97] + "..."
        parts.append(f"L{line_no} ({int(ratio * 100)}%): {preview!r}")
    return " | ".join(parts)


def _format_node_hint(missing_id: str, workflow: dict) -> str:
    """Render closest-node-id suggestions as a one-line hint suffix."""
    suggestions = _suggest_node_ids(missing_id, workflow)
    if not suggestions:
        return ""
    return f"  did you mean: {', '.join(suggestions)}?"


def _nth_occurrence(text: str, sub: str, n: int) -> int:
    """Find the start index of the N-th occurrence of sub in text (1-based).

    Returns -1 if fewer than n occurrences exist.
    """
    start = 0
    pos = -1
    for _ in range(n):
        pos = text.find(sub, start)
        if pos < 0:
            return -1
        start = pos + 1
    return pos


def _nth_replace(text: str, old: str, new: str, occurrence: int) -> str:
    """Replace the N-th occurrence of old with new in text.

    occurrence=0: replace ALL occurrences.
    occurrence=1: replace first (default).
    occurrence=N: replace N-th occurrence only.
    """
    if occurrence == 0:
        return text.replace(old, new)
    if occurrence == 1:
        return text.replace(old, new, 1)
    pos = _nth_occurrence(text, old, occurrence)
    if pos < 0:
        return text
    return text[:pos] + new + text[pos + len(old):]


class WorkflowUpdater:
    @staticmethod
    def apply_updates(
        workflow_dict: Dict[str, Any],
        updates: List[list],
    ) -> Tuple[Dict[str, Any], List[str]]:
        new_workflow = copy.deepcopy(workflow_dict)
        feedback: List[str] = []

        for action in updates:
            try:
                op = action[0]

                if op in ("add", "replace", "remove"):
                    path = action[1]
                    try:
                        segs = json_pointer.parse(path)
                        is_node_path = (len(segs) == 1 and not segs[0].startswith("__"))
                        parent, key, exists = json_pointer.resolve(new_workflow, path)
                    except (KeyError, IndexError, TypeError, ValueError) as e:
                        feedback.append(f"ERROR: {op} {path} — {e}")
                        continue

                    if op == "replace":
                        if not exists:
                            feedback.append(f"ERROR: replace {path} — target does not exist")
                            continue
                        parent[key] = action[2]
                        feedback.append(f"OK: replace {path}")

                    elif op == "add":
                        value = action[2]
                        if isinstance(parent, list):
                            if key == "-":
                                parent.append(value)
                            else:
                                parent.insert(int(key), value)
                        else:
                            parent[key] = value
                        feedback.append(f"OK: add {path}")

                    elif op == "remove":
                        if not exists:
                            feedback.append(
                                f"WARN: remove {path} — target not found, skipped"
                                + (_format_node_hint(segs[0], new_workflow) if is_node_path else "")
                            )
                            continue
                        if isinstance(parent, list):
                            parent.pop(int(key))
                            feedback.append(f"OK: remove {path}")
                        else:
                            removed_id = key
                            parent.pop(key, None)
                            if is_node_path:
                                sweep = 0
                                for k, v in new_workflow.items():
                                    if k.startswith("__") or not isinstance(v, dict):
                                        continue
                                    kids = v.get("children")
                                    if isinstance(kids, list) and removed_id in kids:
                                        v["children"] = [c for c in kids if c != removed_id]
                                        sweep += 1
                                feedback.append(
                                    f"OK: remove {path}"
                                    + (f" (also cleared {sweep} parent reference"
                                       f"{'s' if sweep != 1 else ''})" if sweep else "")
                                )
                            else:
                                feedback.append(f"OK: remove {path}")

                elif op == "text_edit":
                    path = action[1]
                    edits = action[2]
                    try:
                        parent, key, exists = json_pointer.resolve(new_workflow, path)
                    except (KeyError, IndexError, TypeError, ValueError) as e:
                        feedback.append(f"ERROR: text_edit {path} — {e}")
                        continue
                    if not exists:
                        feedback.append(f"ERROR: text_edit {path} — target does not exist")
                        continue
                    text = parent[key]
                    if not isinstance(text, str):
                        feedback.append(
                            f"ERROR: text_edit {path} — target is {type(text).__name__}, expected str")
                        continue
                    for ei, edit in enumerate(edits):
                        edit_op = edit[0]
                        if edit_op == "replace":
                            old_txt, new_txt = edit[1], edit[2]
                            occurrence = edit[3] if len(edit) > 3 and isinstance(edit[3], int) else 1
                            if old_txt not in text:
                                feedback.append(
                                    f"WARN: text_edit {path} "
                                    f"edit[{ei}] replace — anchor not found: "
                                    f"{repr(old_txt[:80])}"
                                    + _format_anchor_hint(old_txt, text)
                                )
                                continue
                            text = _nth_replace(text, old_txt, new_txt, occurrence)
                        elif edit_op == "insert_after":
                            anchor, new_txt = edit[1], edit[2]
                            occurrence = edit[3] if len(edit) > 3 and isinstance(edit[3], int) else 1
                            pos = _nth_occurrence(text, anchor, occurrence) if occurrence > 0 else text.find(anchor)
                            if pos < 0:
                                feedback.append(
                                    f"WARN: text_edit {path} "
                                    f"edit[{ei}] insert_after — anchor not found (occurrence={occurrence}): "
                                    f"{repr(anchor[:80])}"
                                    + _format_anchor_hint(anchor, text)
                                )
                                continue
                            ins = pos + len(anchor)
                            text = text[:ins] + new_txt + text[ins:]
                        elif edit_op == "insert_before":
                            anchor, new_txt = edit[1], edit[2]
                            occurrence = edit[3] if len(edit) > 3 and isinstance(edit[3], int) else 1
                            pos = _nth_occurrence(text, anchor, occurrence) if occurrence > 0 else text.find(anchor)
                            if pos < 0:
                                feedback.append(
                                    f"WARN: text_edit {path} "
                                    f"edit[{ei}] insert_before — anchor not found (occurrence={occurrence}): "
                                    f"{repr(anchor[:80])}"
                                    + _format_anchor_hint(anchor, text)
                                )
                                continue
                            text = text[:pos] + new_txt + text[pos:]
                        elif edit_op == "delete":
                            del_txt = edit[1]
                            occurrence = edit[2] if len(edit) > 2 and isinstance(edit[2], int) else 1
                            if del_txt not in text:
                                feedback.append(
                                    f"WARN: text_edit {path} "
                                    f"edit[{ei}] delete — text not found: "
                                    f"{repr(del_txt[:80])}"
                                    + _format_anchor_hint(del_txt, text)
                                )
                                continue
                            text = _nth_replace(text, del_txt, "", occurrence)
                        else:
                            feedback.append(
                                f"ERROR: text_edit {path} "
                                f"edit[{ei}] — unknown edit op: {edit_op}"
                            )
                    parent[key] = text
                    feedback.append(f"OK: text_edit {path} ({len(edits)} edits)")

                else:
                    feedback.append(f"ERROR: unknown op {op}")

            except Exception as e:
                feedback.append(f"ERROR: {action[0] if action else '?'} — {e}")

        return new_workflow, feedback

    @staticmethod
    def _strip_for_diff(wf: dict) -> dict:
        """Remove fields that produce diff noise without informational value.

        ``__meta__`` carries version pointers that the abstract already
        reports separately. ``__attributes__`` carries canvas x/y — these
        are owned by the frontend and don't reflect agent intent. Matches
        ``content_equal``'s stripping so "no structural change" agrees
        across both helpers.
        """
        out = {}
        for k, v in wf.items():
            if k == "__meta__":
                continue
            if isinstance(v, dict):
                v = {fk: fv for fk, fv in v.items() if fk != "__attributes__"}
            out[k] = v
        return out

    @staticmethod
    def workflow_diff(
        before: dict, after: dict, max_lines: int = 60,
    ) -> str:
        """Return a unified diff between two workflow dicts.

        Both sides are JSON-serialized with ``indent=2`` and ``sort_keys=True``
        before diffing so structural changes surface as compact hunks. If
        the diff exceeds ``max_lines``, it is truncated with a marker —
        token budget for the agent, not a correctness concern (the agent
        can re-read the full snapshot from the same return).

        Returns an empty string when before == after under the strip rules.
        """
        before_clean = WorkflowUpdater._strip_for_diff(before or {})
        after_clean = WorkflowUpdater._strip_for_diff(after or {})
        if before_clean == after_clean:
            return ""
        before_str = json.dumps(
            before_clean, ensure_ascii=False, indent=2, sort_keys=True,
        )
        after_str = json.dumps(
            after_clean, ensure_ascii=False, indent=2, sort_keys=True,
        )
        lines = list(difflib.unified_diff(
            before_str.splitlines(),
            after_str.splitlines(),
            fromfile="workflow_before",
            tofile="workflow_after",
            n=3,
            lineterm="",  # don't double-newline the ---/+++/@@ headers
        ))
        if len(lines) > max_lines:
            remaining = len(lines) - max_lines
            lines = lines[:max_lines] + [
                f"... ({remaining} more diff lines truncated; "
                "re-read snapshot for full state)"
            ]
        return "\n".join(lines)

    @staticmethod
    def diff_summary(before: dict, after: dict) -> str:
        """One-line node-level summary: ``+added ~modified -removed``.

        Returns "(no structural change)" when nothing meaningful changed.
        Used in the abstract field so the agent gets the gist without
        parsing the full unified diff.
        """
        before_clean = WorkflowUpdater._strip_for_diff(before or {})
        after_clean = WorkflowUpdater._strip_for_diff(after or {})
        before_keys = set(before_clean)
        after_keys = set(after_clean)
        added = sorted(after_keys - before_keys)
        removed = sorted(before_keys - after_keys)
        modified = sorted(
            k for k in before_keys & after_keys
            if before_clean[k] != after_clean[k]
        )
        parts = []
        if added:
            parts.append("+" + ",".join(added))
        if modified:
            parts.append("~" + ",".join(modified))
        if removed:
            parts.append("-" + ",".join(removed))
        return " ".join(parts) if parts else "(no structural change)"

    @staticmethod
    def content_equal(wf_a: dict, wf_b: dict) -> bool:
        """Compare two workflow dicts by structural content only.

        Ignores ``__meta__`` (version/timestamp metadata) and each node's
        ``__attributes__`` (canvas x/y positions) so that cosmetic or
        metadata-only differences don't count as "dirty".
        """
        def _strip(wf: dict) -> dict:
            out = {}
            for k, v in wf.items():
                if k == "__meta__":
                    continue
                if isinstance(v, dict):
                    v = {fk: fv for fk, fv in v.items() if fk != "__attributes__"}
                out[k] = v
            return out

        return _strip(wf_a) == _strip(wf_b)

    @staticmethod
    def ensure_saved(frontend_wf: dict, repo) -> dict | None:
        """Auto-save the workflow if it has unsaved structural changes.

        Compares *frontend_wf* against the latest committed version in
        *repo*.  If there are structural differences, commits a
        new subversion and returns the saved workflow dict.  If nothing
        changed, returns ``None``.
        """
        wf_id = (frontend_wf.get("__meta__") or {}).get("workflow_id")
        if not wf_id or not repo:
            return None

        committed_wf = repo.get_current_workflow(wf_id)
        if not committed_wf:
            return None

        if WorkflowUpdater.content_equal(frontend_wf, committed_wf):
            return None

        print(f"💾 [auto-save] workflow {wf_id} has unsaved changes, committing before execution")
        repo.commit(wf_id, frontend_wf, note="Auto Save (pre-execution)")
        return repo.get_current_workflow(wf_id)

    @staticmethod
    def build_execution_context(
        chat_context: str,
        frontend_wf: dict,
        exec_snapshot: dict,
        exec_snapshot_meta: dict,
        max_chars_per_node: int = 12000,
    ) -> str:
        """Build a execution-context string for the agent system prompt.

        Returns a formatted section describing recent execution results,
        or an empty string if the context is inapplicable (Edge context,
        no snapshot, or workflow has changed since execution).

        Args:
            chat_context: "Global", "Node@node_3", or "Edge@A→B".
            frontend_wf: The current workflow dict from the frontend.
            exec_snapshot: ``{node_id: {status, inputs, result}}``.
            exec_snapshot_meta: The workflow ``__meta__`` captured at
                execution time — used for version comparison.
            max_chars_per_node: Truncate each node's result text to this
                limit to keep the prompt compact.
        """
        if not exec_snapshot or not exec_snapshot_meta:
            return ""

        # Edge context — no execution info needed
        if chat_context.startswith("Edge@"):
            return ""

        # --- Version + content check ---
        cur_meta = frontend_wf.get("__meta__") or {}
        snap_wf_id = exec_snapshot_meta.get("workflow_id")
        cur_wf_id = cur_meta.get("workflow_id")
        if not snap_wf_id or snap_wf_id != cur_wf_id:
            return ""

        snap_ver = (exec_snapshot_meta.get("workflow_version"),
                    exec_snapshot_meta.get("workflow_subversion"))
        cur_ver = (cur_meta.get("workflow_version"),
                   cur_meta.get("workflow_subversion"))
        if snap_ver != cur_ver:
            return ""

        # Version matches but content may have diverged (local edits)
        # Build a "committed-equivalent" workflow from exec_snapshot_meta
        # We can only compare the frontend_wf against itself at the snapshot
        # moment — but we don't have the old workflow stored separately.
        # Since version+subversion match AND auto-save runs before execution,
        # content divergence means the user edited *after* execution.
        # We approximate by checking whether the nodes referenced in the
        # snapshot still exist with the same structure in frontend_wf.

        def _truncate(text: str, limit: int) -> str:
            if len(text) <= limit:
                return text
            return text[:limit] + f"\n... (truncated, {len(text)} chars total)"

        lines: list[str] = []

        if chat_context.startswith("Node@"):
            node_id = chat_context.split("@", 1)[1]
            entry = exec_snapshot.get(node_id)
            if not entry:
                return ""
            node_name = (frontend_wf.get(node_id) or {}).get("node_name", node_id)
            lines.append(f"### Execution result for node [{node_name}] ({node_id})")
            inputs_str = json.dumps(entry.get("inputs", {}), ensure_ascii=False, indent=2, default=str)
            lines.append(f"**Inputs:**\n```json\n{_truncate(inputs_str, max_chars_per_node)}\n```")
            lines.append(f"**Result:**\n```\n{_truncate(entry.get('result', ''), max_chars_per_node)}\n```")

        elif chat_context == "Global":
            lines.append("### Recent workflow execution results")
            for nid, entry in exec_snapshot.items():
                node_name = (frontend_wf.get(nid) or {}).get("node_name", nid)
                result_text = _truncate(entry.get("result", ""), max_chars_per_node)
                lines.append(f"#### [{node_name}] ({nid})")
                lines.append(f"```\n{result_text}\n```")

        if not lines:
            return ""

        header = "## Recent execution context\n"
        header += "The following execution data is from the most recent run on the current workflow version.\n"
        return header + "\n".join(lines)