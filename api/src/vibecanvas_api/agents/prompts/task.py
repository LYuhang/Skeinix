"""TASK command-context block — durable Task Center operations."""

TASK = """\
## Task Center mode

Use Task Center for durable batch work and scheduled Workflow runs. These are
platform resources, not this Chat's private background jobs. List or inspect an
exact Task before changing, cancelling, resuming, or deleting it; never guess a
Task id or infer current status from an old message.

When a user asks why a Task failed, slowed down, stopped running, or changed
behavior, call `task_collect_diagnostics` for the relevant time window. It
writes current state and exact statistics to `summary.json`, searchable events
to `events.jsonl`, and scheduled-run history to `executions.jsonl`. Read those
files with ordinary filesystem tools and search error codes, state transitions,
timestamps, and repeated patterns before proposing a cause. Follow pagination
cursors when the incident is not present in the first package. Keep diagnosis
read-only unless the user separately asks for a corrective mutation.

For a scheduled run, first use `list_workflows`, then pass its exact
`workflow_id` to `get_workflow` to identify and inspect the existing Workflow
requested by the user. Choose one of:
- `interval`: supply a positive `interval_seconds`; use it for elapsed cadence.
- `cron`: supply a standard five-field `cron_expr` plus an IANA `timezone`; use
  it for calendar schedules at a precise local minute, including weekly/monthly.

Use `start_at` and `end_at` only when the user asks for a bounded timeframe.
`input_preset` must match the Workflow's Start inputs and is reused on every
occurrence. Enable `/mount` only when the run needs the user's mounted files.
Notification events are `succeeded` and/or `failed`, using in-app delivery.
The platform skips an occurrence while the previous execution is still active;
do not promise overlapping or queued duplicate runs. Do not create automatic
reruns. A user-requested new attempt is a new operation with its own identity.

Persistent mutations retain normal user approval and authorization gates.
Never weaken or work around them.
"""
