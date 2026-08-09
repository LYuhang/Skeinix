"""PLAN command context — strict static fork/join execution graphs."""

PLAN = r"""\
## Static Execution Plan

A Plan is an immutable, one-run DAG of background subagent tasks. It is not a
canvas Workflow. Write one strict JSON file to
`/data/plans/<name>.plan.json`, then call `create_execution_plan(plan_path)`.

### Protocol

```json
{
  "schema_version": 1,
  "title": "Short title",
  "nodes": [],
  "budgets": {"max_wall_time_seconds": 1800}
}
```

The graph has 2–30 nodes, exactly one `start` and one `end`, unique lowercase
snake_case IDs, no cycles, and no unreachable nodes. Allowed nodes:

- `start`: `{ "id", "type":"start", "title"?, "next":[node_id,...] }`
- `subagent`: `{ "id", "type":"subagent", "title", "task",
  "next":[node_id,...] }`
- `end`: `{ "id", "type":"end", "title"? }`

Every subagent task must be self-contained. Put known facts directly in
`task`. For inter-node handoff, declare the same fixed absolute VFS path in the
writer and reader prompts, for example `/data/plan-work/research.md`. The
platform also writes all terminal node results to
`/data/plans/runs/<plan_run_id>/results.json` and exposes that path with the
completed background task.

### Fork/join rules

- Multiple targets in `next` create a fork.
- Multiple incoming edges create a join; it runs only after every predecessor
  succeeds.
- Every fork must converge at one nearest common join. Branches stay disjoint
  until that join and cannot escape to different terminal paths.
- Fork/join regions may be nested but cannot cross. `end` may be the final join.
- A failed or cancelled branch skips its dependent join and downstream nodes.

### Example

```json
{
  "schema_version": 1,
  "title": "Research and synthesize",
  "nodes": [
    {"id":"start","type":"start","next":["official","code"]},
    {"id":"official","type":"subagent","title":"Official research","task":"Research official sources and write /data/plan-work/official.md.","next":["synthesize"]},
    {"id":"code","type":"subagent","title":"Code review","task":"Inspect the code and write /data/plan-work/code.md.","next":["synthesize"]},
    {"id":"synthesize","type":"subagent","title":"Synthesize","task":"Read /data/plan-work/official.md and /data/plan-work/code.md; write the final report to /data/plan-work/final.md and return that path.","next":["end"]},
    {"id":"end","type":"end"}
  ],
  "budgets":{"max_wall_time_seconds":1200}
}
```

Create validates JSON, schema, topology, fork/join structure and graph timeout.
On failure, repair the same file using each issue's `node_id`, `json_pointer`,
`message`, and `suggested_fix`, then submit again. Fork/join errors use stable
codes including `split_without_merge`, `split_merge_not_join`,
`split_branches_overlap_before_merge`, and `crossing_split_merge_scopes`.
"""
