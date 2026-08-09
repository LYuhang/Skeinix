/**
 * `/tasks/:taskId` task detail page.
 *
 * Two data sources, one screen:
 *   * TanStack Query polls `GET /tasks/{id}` for the canonical row
 *     (status, progress, result summary, results_uri). Polling pauses
 *     when the task reaches a terminal state — there is nothing left to
 *     refresh, and a stale 5s tick wastes a request.
 *   * `useTaskStream` opens the SSE channel for the live event log
 *     (`progress`, `started`, `finished`, `cancelled`, `error`,
 *     and any custom worker emissions).
 *
 * Why two channels instead of "compute everything from SSE":
 *   * The polled GET is RLS-bound to the same tenant + reuses the
 *     auth middleware (the 401 dialog), and it stays consistent across
 *     reconnects.
 *   * SSE is a delta channel — the worker emits `progress` frames as
 *     batch rows finish, but the canonical `progress` value lives in
 *     `tasks.progress` and is what the list page shows. Reading both
 *     and trusting the polled value avoids drift if the user opens
 *     this page after a stream drop.
 *
 * Cancel UX: one user-facing Cancel action safely stops the batch and preserves
 * partial artifacts. Internal cleanup/escalation details are not exposed.
 *
 * Download UX:
 *   * Result-bearing rows expose CSV, JSONL, and on-demand Excel downloads.
 */
import { useEffect, useMemo, useState } from "react";
import { useParams, Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ChevronDown, Download, FolderOpen, ListChecks, Share2 } from "lucide-react";

import { EntityDetailShell } from "@/components/layout/entity-detail-shell";
import { ResourceShareDialog } from "@/components/modals/ResourceShareDialog";
import { ActionableError } from "@/components/presentation/ActionableError";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  ProgressState,
  StatusBadge,
  type SemanticStatus,
} from "@/components/ui/status";
import {
  cancelTask,
  cancelScheduledExecution,
  getTask,
  getScheduledRun,
  listScheduledRunExecutions,
  pauseScheduledRun,
  resumeTask,
  resumeScheduledRun,
  runScheduledNow,
  type ScheduledRunExecution,
  type TaskEventPayload,
  type TaskEventType,
  type Task,
  type TaskStatus,
} from "@/lib/api/tasks";
import { useTaskStream, type TaskEventFrame } from "@/lib/api/sse/run-task-stream";
import { useFormatDateTime } from "@/lib/timezone";

const POLL_INTERVAL_MS = 5_000;
const ACTIVE_STATUSES: TaskStatus[] = ["queued", "running", "resuming", "cancelling"];
const CANCELLABLE: TaskStatus[] = ["queued", "running", "resuming"];
const RESUMABLE: TaskStatus[] = ["cancelled", "failed", "interrupted", "finished_with_errors"];
const EVENT_TYPE_OPTIONS: TaskEventType[] = ["state", "progress", "log", "result", "terminal"];
const LEVEL_OPTIONS = ["all", "info", "warning", "error", "debug"] as const;

function taskStatusTone(s: TaskStatus): SemanticStatus {
  switch (s) {
    case "queued":
      return "neutral";
    case "running":
    case "resuming":
      return "running";
    case "finished":
      return "success";
    case "finished_with_errors":
    case "interrupted":
    case "cancelling":
      return "warning";
    case "failed":
      return "danger";
    case "cancelled":
    case "paused":
      return "neutral";
    case "enabled":
      return "success";
  }
}

/** Keep cleanup mechanics out of the user-facing task vocabulary. */
function visibleTaskStatus(status: TaskStatus): TaskStatus {
  return status === "cancelling" || status === "interrupted"
    ? "cancelled"
    : status;
}

function taskDisplayName(task: Task, fallback: string): string {
  const payload = task.payload && typeof task.payload === "object"
    ? task.payload as Record<string, unknown>
    : {};
  return (
    (typeof payload.name === "string" && payload.name.trim()) || fallback
  );
}

function executionStatusTone(
  s: ScheduledRunExecution["status"],
): SemanticStatus {
  switch (s) {
    case "queued":
      return "neutral";
    case "running":
      return "running";
    case "succeeded":
      return "success";
    case "failed":
      return "danger";
    case "cancelled":
    case "skipped":
      return "neutral";
  }
}

function eventLevelTone(level: string): SemanticStatus {
  if (level === "error") return "danger";
  if (level === "warning") return "warning";
  if (level === "debug") return "neutral";
  return "info";
}

/** Narrow the unknown `result` blob to the batch-run summary shape. */
interface BatchSummary {
  rows_total?: number;
  rows_ok?: number;
  rows_failed?: number;
}

function asBatchSummary(r: unknown): BatchSummary | null {
  if (!r || typeof r !== "object") return null;
  const o = r as Record<string, unknown>;
  const out: BatchSummary = {};
  if (typeof o.rows_total === "number") out.rows_total = o.rows_total;
  if (typeof o.rows_ok === "number") out.rows_ok = o.rows_ok;
  if (typeof o.rows_failed === "number") out.rows_failed = o.rows_failed;
  return Object.keys(out).length > 0 ? out : null;
}

function isTaskStatus(value: unknown): value is TaskStatus {
  return typeof value === "string" && [
    "queued",
    "running",
    "resuming",
    "finished",
    "finished_with_errors",
    "failed",
    "interrupted",
    "cancelling",
    "cancelled",
    "enabled",
    "paused",
  ].includes(value);
}

function eventTaskPatch(task: Task, frame: TaskEventFrame): Partial<Task> | null {
  const payload = frame.payload;
  const patch: Partial<Task> = {};

  if (isTaskStatus(payload.task_status)) {
    patch.status = payload.task_status;
  }
  if (payload.sandbox_status && typeof payload.sandbox_status === "string") {
    patch.sandbox_status = payload.sandbox_status as Task["sandbox_status"];
  }
  if (typeof payload.progress?.percent === "number") {
    patch.progress = Math.max(0, Math.min(1, payload.progress.percent));
  }
  if (payload.error?.message) {
    patch.error = payload.error.message;
  }

  const data = payload.data && typeof payload.data === "object"
    ? payload.data as Record<string, unknown>
    : {};
  if (typeof data.results_uri === "string") {
    patch.results_uri = data.results_uri;
  }
  if (data.summary && typeof data.summary === "object") {
    patch.result = data.summary;
  }

  if (payload._event_ts) {
    if (patch.status === "running" && !task.started_at) {
      patch.started_at = payload._event_ts;
    }
    if (frame.event_type === "terminal" && !task.finished_at) {
      patch.finished_at = payload._event_ts;
    }
  }

  return Object.keys(patch).length > 0 ? patch : null;
}

/**
 * Pull `{done, total}` from the most recent `progress` SSE frame. The batch
 * worker emits `progress` frames carrying per-row counts as rows finish — we
 * surface them as a plain "X of Y rows done" line so a non-technical user
 * reads their batch at a glance instead of decoding a bare percentage.
 * Returns null until a usable progress frame arrives.
 */
function latestRowCounts(
  events: TaskEventFrame[],
): { done: number; total: number } | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const f = events[i];
    if (f.event_type !== "progress") continue;
    const p = f.payload.progress;
    if (p && typeof p.done === "number" && typeof p.total === "number") {
      return { done: p.done, total: p.total };
    }
  }
  return null;
}

/** Render one event frame as a compact log row. */
function EventRow({
  frame,
  formatTime,
  nowLabel,
}: {
  frame: TaskEventFrame;
  formatTime: (value?: string | null) => string;
  nowLabel: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const payload = frame.payload;
  const level = payload.level ?? "info";
  const message = payload.message || payload.error?.message || payload.action || frame.event_type;
  const scope = payload.scope
    ? [payload.scope.type, payload.scope.id].filter(Boolean).join(":")
    : "";
  const payloadStr = useMemo(() => {
    try {
      return JSON.stringify(
        {
          progress: payload.progress,
          data: payload.data,
          error: payload.error,
        },
        null,
        2,
      );
    } catch {
      return "";
    }
  }, [payload]);

  return (
    <li className="border-b py-2 last:border-b-0">
      <button
        type="button"
        className="grid w-full grid-cols-[56px_128px_88px_128px_1fr] items-start gap-3 text-left"
        onClick={() => setExpanded((v) => !v)}
      >
        <span className="font-mono text-xs tabular-nums text-muted-foreground">
        {frame.id}
      </span>
        <span className="text-xs text-muted-foreground">
          {(payload as TaskEventPayload & { _event_ts?: string })._event_ts
            ? formatTime((payload as TaskEventPayload & { _event_ts?: string })._event_ts)
            : nowLabel}
        </span>
        <StatusBadge className="w-fit" status={eventLevelTone(level)}>
          {level}
        </StatusBadge>
        <span className="font-mono text-xs text-muted-foreground">
          {payload.action ?? frame.event_type}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-xs font-medium">{message}</span>
          {scope && (
            <span className="mt-0.5 block truncate font-mono text-xs text-muted-foreground">
              {scope}
            </span>
          )}
        </span>
      </button>
      {expanded && payloadStr !== "{}" && (
        <pre className="mt-2 max-h-56 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-100">
          {payloadStr}
        </pre>
      )}
    </li>
  );
}

export function TaskDetailPage() {
  const { t } = useTranslation();
  // Render UTC timestamps in the user's chosen timezone (reactive).
  const formatTime = useFormatDateTime();
  const { taskId } = useParams<{ taskId: string }>();
  const qc = useQueryClient();
  const [shareOpen, setShareOpen] = useState(false);
  const [eventTypeFilter, setEventTypeFilter] = useState<TaskEventType | "all">("all");
  const [levelFilter, setLevelFilter] = useState<(typeof LEVEL_OPTIONS)[number]>("all");
  const [selectedExecutionId, setSelectedExecutionId] = useState<string | null>(null);

  const taskQuery = useQuery({
    queryKey: ["task", taskId],
    queryFn: () => getTask(taskId!),
    enabled: !!taskId,
    // Stop polling once the row reaches a terminal state — nothing
    // left to refresh and we want to be a good network citizen.
    refetchInterval: (q) => {
      const data = q.state.data as Task | undefined;
      if (!data) return POLL_INTERVAL_MS;
      return ACTIVE_STATUSES.includes(data.status)
        ? POLL_INTERVAL_MS
        : false;
    },
    refetchOnWindowFocus: false,
  });

  const stream = useTaskStream(
    taskId,
    taskQuery.data?.access?.capabilities.includes("inspect_runs") ?? false,
  );
  const scheduledQuery = useQuery({
    queryKey: ["task", taskId, "scheduled-run"],
    queryFn: () => getScheduledRun(taskId!),
    enabled: !!taskId && taskQuery.data?.task_type === "scheduled_run",
    refetchInterval: (q) => {
      const data = q.state.data;
      return data?.task.status === "running" ? POLL_INTERVAL_MS : false;
    },
    refetchOnWindowFocus: false,
  });
  const executionsQuery = useQuery({
    queryKey: ["task", taskId, "scheduled-run", "executions"],
    queryFn: () => listScheduledRunExecutions(taskId!, { limit: 50 }),
    enabled: !!taskId && taskQuery.data?.task_type === "scheduled_run",
    refetchInterval: (q) => {
      const data = q.state.data;
      return data?.items.some((x) => x.status === "running" || x.status === "queued")
        ? POLL_INTERVAL_MS
        : false;
    },
    refetchOnWindowFocus: false,
  });
  const executions = useMemo(() => executionsQuery.data?.items ?? [], [executionsQuery.data?.items]);
  const selectedExecution = useMemo(() => {
    if (!executions.length) return null;
    return executions.find((x) => x.id === selectedExecutionId) ?? executions[0];
  }, [executions, selectedExecutionId]);

  useEffect(() => {
    if (!taskId || stream.events.length === 0) return;
    const frame = stream.events[stream.events.length - 1];
    qc.setQueryData<Task>(["task", taskId], (old) => {
      if (!old) return old;
      const patch = eventTaskPatch(old, frame);
      return patch ? { ...old, ...patch } : old;
    });
    if (frame.event_type === "terminal") {
      void qc.invalidateQueries({ queryKey: ["tasks"] });
      void qc.invalidateQueries({ queryKey: ["task", taskId] });
      if (taskQuery.data?.task_type === "scheduled_run") {
        void qc.invalidateQueries({ queryKey: ["task", taskId, "scheduled-run"] });
        void qc.invalidateQueries({ queryKey: ["task", taskId, "scheduled-run", "executions"] });
      }
    }
  }, [qc, stream.events, taskId, taskQuery.data?.task_type]);

  const visibleEvents = useMemo(
    () =>
      stream.events.filter((event) => {
        if (eventTypeFilter !== "all" && event.event_type !== eventTypeFilter) return false;
        const level = event.payload.level ?? "info";
        if (levelFilter !== "all" && level !== levelFilter) return false;
        if (taskQuery.data?.task_type === "scheduled_run" && selectedExecution) {
          const data = event.payload.data;
          const scope = event.payload.scope;
          const matchesData =
            data && typeof data === "object" &&
            (data as Record<string, unknown>).execution_id === selectedExecution.id;
          const matchesScope =
            scope && typeof scope === "object" &&
            scope.id === selectedExecution.id;
          if (!matchesData && !matchesScope) return false;
        }
        return true;
      }),
    [eventTypeFilter, levelFilter, selectedExecution, stream.events, taskQuery.data?.task_type],
  );

  const cancelMutation = useMutation({
    mutationFn: () => cancelTask(taskId!, "soft"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["task", taskId] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      toast.success(t("tasks.cancel_requested", "Cancel requested"));
    },
    onError: (e) => {
      toast.error(
        `${t("tasks.cancel_failed", "Cancel failed")}: ${
          e instanceof Error ? e.message : String(e)
        }`,
      );
    },
  });

  const resumeMutation = useMutation({
    mutationFn: () => resumeTask(taskId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["task", taskId] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      toast.success(t("taskDetail.resumeRequested", "Resume requested"));
    },
    onError: (e) => {
      toast.error(
        `${t("taskDetail.resumeFailed", "Resume failed")}: ${
          e instanceof Error ? e.message : String(e)
        }`,
      );
    },
  });

  const runNowMutation = useMutation({
    mutationFn: () => runScheduledNow(taskId!),
    onSuccess: (data) => {
      setSelectedExecutionId(data.execution.id);
      qc.invalidateQueries({ queryKey: ["task", taskId] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      toast.success(t("tasks.scheduled.runNowQueued", "Run now queued"));
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });

  const pauseScheduleMutation = useMutation({
    mutationFn: () => pauseScheduledRun(taskId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["task", taskId] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      toast.success(t("tasks.scheduled.paused", "Schedule paused"));
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });

  const resumeScheduleMutation = useMutation({
    mutationFn: () => resumeScheduledRun(taskId!),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["task", taskId] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      toast.success(t("tasks.scheduled.resumed", "Schedule resumed"));
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });

  const cancelExecutionMutation = useMutation({
    mutationFn: (executionId: string) => cancelScheduledExecution(taskId!, executionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["task", taskId] });
      qc.invalidateQueries({ queryKey: ["tasks"] });
      toast.success(t("tasks.cancel_requested", "Cancel requested"));
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : String(e)),
  });

  if (!taskId) {
    return (
      <div className="flex-1 p-6 text-sm text-muted-foreground">
        {t("taskDetail.invalidId", "Missing task id in URL")}
      </div>
    );
  }

  if (taskQuery.isLoading) {
    return (
      <div className="flex-1 p-6 text-sm text-muted-foreground">
        {t("tasks.loading", "Loading…")}
      </div>
    );
  }

  if (taskQuery.isError || !taskQuery.data) {
    return (
      <div className="flex-1 p-6">
        <ActionableError
          title={t(
            "taskDetail.loadError",
            "Failed to load this task. It may have been deleted or you may not have access.",
          )}
          description={t("taskDetail.loadErrorHint", "Return to the task list or try loading this task again.")}
          actionLabel={t("retry", "Retry")}
          onAction={() => void taskQuery.refetch()}
          technicalDetails={taskQuery.error instanceof Error ? taskQuery.error.message : String(taskQuery.error ?? "")}
          technicalDetailsLabel={t("technicalDetails", "Technical details")}
        />
        <div className="mt-4">
          <Link
            to="/tasks"
            className="text-sm text-primary underline-offset-4 hover:underline"
          >
            ← {t("taskDetail.backToList", "Back to tasks")}
          </Link>
        </div>
      </div>
    );
  }

  const task = taskQuery.data;
  const displayStatus = visibleTaskStatus(task.status);
  const capabilities = new Set(task.access?.capabilities ?? []);
  const pct = Math.round((task.progress ?? 0) * 100);
  const isScheduledRun = task.task_type === "scheduled_run";
  const isCancellable = !isScheduledRun && capabilities.has("cancel") && CANCELLABLE.includes(task.status);
  const isResumable = !isScheduledRun && capabilities.has("resume") && RESUMABLE.includes(task.status) && !!(task.result as { artifact_uris?: { jsonl?: string } } | null)?.artifact_uris?.jsonl;
  const summary = ["finished", "finished_with_errors", "interrupted"].includes(task.status)
    ? asBatchSummary(task.result)
    : null;
  // "X of Y rows done" — prefer the live SSE progress frame; once finished,
  // the summary card carries the authoritative totals so we pin done==total.
  const liveCounts = latestRowCounts(stream.events);
  const rowCounts = summary && task.status !== "interrupted"
    ? typeof summary.rows_total === "number"
      ? { done: summary.rows_total, total: summary.rows_total }
      : null
    : liveCounts;
  const canDownload = !isScheduledRun && capabilities.has("export") && !!task.results_uri;
  const downloadHref = `/api/v1/tasks/${taskId}/download`;
  const storageHref = `/storage?path=${encodeURIComponent(`/task/${task.id}`)}`;

  return (
    <EntityDetailShell
      resourceKind="task"
      backTo="/tasks"
      backLabel={t("taskDetail.backToList", "Back to tasks")}
      title={taskDisplayName(
        task,
        task.task_type === "scheduled_run"
          ? t("tasks.type.scheduled_run", "Scheduled run")
          : t("tasks.type.batch_exec", "Batch execution"),
      )}
      description={t(`tasks.type.${task.task_type}`, task.task_type)}
      icon={ListChecks}
      status={
        <StatusBadge status={taskStatusTone(displayStatus)}>
          {t(`tasks.status.${displayStatus}`, displayStatus)}
        </StatusBadge>
      }
      metadata={(
        <span className="font-mono">
          {t("taskDetail.taskId", "Task ID")}: {task.id.slice(0, 8)}…
          {task.workflow_id ? ` · ${t("tasks.col.workflow", "Workflow")}: ${task.workflow_id}` : ""}
        </span>
      )}
      actions={
        <>
              {canDownload && (
                <>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="outline" size="sm">
                        <Download />
                        {t("taskDetail.download", "Download")}
                        <ChevronDown />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem asChild>
                        <a href={`${downloadHref}?format=csv`}>CSV</a>
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild>
                        <a href={`${downloadHref}?format=jsonl`}>JSONL</a>
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild>
                        <a href={`${downloadHref}?format=xlsx`}>Excel (.xlsx)</a>
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                  <Button variant="outline" size="sm" asChild>
                    <Link to={storageHref}>
                      <FolderOpen />
                      {t("taskDetail.viewInStorage", "View in Storage")}
                    </Link>
                  </Button>
                </>
              )}
              {isCancellable && (
                <>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => cancelMutation.mutate()}
                    disabled={cancelMutation.isPending}
                  >
                    {t("tasks.action.cancel", "Cancel")}
                  </Button>
                </>
              )}
              {isResumable && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => resumeMutation.mutate()}
                  disabled={resumeMutation.isPending}
                >
                  {t("taskDetail.resume", "Resume")}
                </Button>
              )}
              {isScheduledRun && (capabilities.has("execute") || capabilities.has("update") || capabilities.has("cancel")) && (
                <>
                  {capabilities.has("execute") ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => runNowMutation.mutate()}
                      disabled={runNowMutation.isPending || task.status === "running"}
                    >
                      {t("tasks.scheduled.runNow", "Run now")}
                    </Button>
                  ) : null}
                  {capabilities.has("update") ? task.status === "paused" ? (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => resumeScheduleMutation.mutate()}
                        disabled={resumeScheduleMutation.isPending}
                      >
                        {t("tasks.scheduled.resume", "Resume schedule")}
                      </Button>
                    ) : (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => pauseScheduleMutation.mutate()}
                        disabled={pauseScheduleMutation.isPending}
                      >
                        {t("tasks.scheduled.pause", "Pause schedule")}
                      </Button>
                    ) : null}
                  {selectedExecution?.status === "running" && capabilities.has("cancel") && (
                    <Button
                      variant="destructive"
                      size="sm"
                      onClick={() => cancelExecutionMutation.mutate(selectedExecution.id)}
                      disabled={cancelExecutionMutation.isPending}
                    >
                      {t("tasks.action.cancel", "Cancel")}
                    </Button>
                  )}
                </>
              )}
              {capabilities.has("manage_access") ? (
                <Button variant="outline" size="sm" onClick={() => setShareOpen(true)}>
                  <Share2 />
                  {t("tasks.action.share", "Share task")}
                </Button>
              ) : null}
        </>
      }
      className="max-w-4xl gap-6"
    >

        {/* Progress + timestamps */}
        <div className="rounded-md border p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex flex-col">
              <span className="text-sm font-medium">
                {t("tasks.col.progress", "Progress")}
              </span>
              {rowCounts && (
                <span
                  className="text-xs text-muted-foreground"
                  data-testid="row-progress"
                >
                  {t(
                    "taskDetail.rowsProgress",
                    "{{done}} of {{total}} rows done",
                    { done: rowCounts.done, total: rowCounts.total },
                  )}
                </span>
              )}
            </div>
            <span className="text-sm tabular-nums text-muted-foreground">
              {pct}%
            </span>
          </div>
          <ProgressState
            status={taskStatusTone(task.status)}
            label={<span className="sr-only">{t("tasks.col.progress", "Progress")}</span>}
            value={pct}
          />
          <dl className="mt-4 grid grid-cols-1 gap-x-6 gap-y-2 text-xs sm:grid-cols-3">
            <div className="flex flex-col">
              <dt className="text-muted-foreground">
                {t("taskDetail.submittedAt", "Submitted")}
              </dt>
              <dd>{formatTime(task.submitted_at)}</dd>
            </div>
            <div className="flex flex-col">
              <dt className="text-muted-foreground">
                {t("taskDetail.startedAt", "Started")}
              </dt>
              <dd>{formatTime(task.started_at)}</dd>
            </div>
            <div className="flex flex-col">
              <dt className="text-muted-foreground">
                {t("taskDetail.finishedAt", "Finished")}
              </dt>
              <dd>{formatTime(task.finished_at)}</dd>
            </div>
          </dl>
        </div>

        {isScheduledRun && (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
            <section className="rounded-md border p-4">
              <h2 className="text-sm font-medium">
                {t("tasks.scheduled.configuration", "Schedule configuration")}
              </h2>
              {scheduledQuery.isLoading ? (
                <div className="mt-3 text-sm text-muted-foreground">
                  {t("tasks.loading", "Loading…")}
                </div>
              ) : scheduledQuery.data ? (
                <dl className="mt-3 grid gap-2 text-sm">
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">{t("tasks.scheduled.name", "Name")}</dt>
                    <dd className="text-right font-medium">{scheduledQuery.data.schedule.name}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">{t("tasks.col.workflow", "Workflow")}</dt>
                    <dd className="font-mono text-xs">{scheduledQuery.data.schedule.workflow_id}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">{t("tasks.scheduled.timing", "Timing")}</dt>
                    <dd className="text-right">
                      {scheduledQuery.data.schedule.schedule_type === "interval"
                        ? `${scheduledQuery.data.schedule.interval_seconds}s`
                        : scheduledQuery.data.schedule.cron_expr}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">{t("tasks.scheduled.timezone", "Timezone")}</dt>
                    <dd>{scheduledQuery.data.schedule.timezone}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">{t("tasks.scheduled.nextRun", "Next run")}</dt>
                    <dd>{formatTime(scheduledQuery.data.schedule.next_run_at)}</dd>
                  </div>
                  <div className="flex justify-between gap-3">
                    <dt className="text-muted-foreground">{t("tasks.scheduled.lastStatus", "Last status")}</dt>
                    <dd>{scheduledQuery.data.schedule.last_status ?? "—"}</dd>
                  </div>
                </dl>
              ) : (
                <div className="mt-3 text-sm text-muted-foreground">
                  {t("taskDetail.loadError", "Failed to load this task. It may have been deleted or you may not have access.")}
                </div>
              )}
            </section>

            <section className="rounded-md border p-4">
              <div className="flex items-center justify-between gap-3">
                <h2 className="text-sm font-medium">
                  {t("tasks.scheduled.runHistory", "Run history")}
                </h2>
                {executionsQuery.isFetching && (
                  <span className="text-xs text-muted-foreground">
                    {t("tasks.loading", "Loading…")}
                  </span>
                )}
              </div>
              {executions.length === 0 ? (
                <div className="mt-3 rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                  {t("tasks.scheduled.noRuns", "No executions yet.")}
                </div>
              ) : (
                <div className="mt-3 max-h-80 overflow-auto rounded-md border">
                  {executions.map((execution) => (
                    <button
                      key={execution.id}
                      type="button"
                      onClick={() => setSelectedExecutionId(execution.id)}
                      className={`grid w-full grid-cols-[96px_1fr_auto] items-center gap-3 border-b px-3 py-2 text-left text-sm last:border-b-0 hover:bg-surface-hover ${
                        selectedExecution?.id === execution.id ? "bg-surface-hover font-medium" : ""
                      }`}
                    >
                      <StatusBadge status={executionStatusTone(execution.status)}>
                        {execution.status}
                      </StatusBadge>
                      <span className="min-w-0">
                        <span className="block truncate text-xs text-muted-foreground">
                          {execution.trigger_type} · {formatTime(execution.triggered_at)}
                        </span>
                        {execution.error && (
                          <span className="mt-0.5 block truncate text-xs text-destructive">
                            {execution.error}
                          </span>
                        )}
                      </span>
                      <span className="font-mono text-xs text-muted-foreground">
                        {execution.id.slice(0, 8)}
                      </span>
                    </button>
                  ))}
                </div>
              )}
              {selectedExecution && (
                <div className="mt-3 rounded-md bg-surface-sunken p-3 text-xs">
                  <div className="font-medium text-foreground">
                    {t("tasks.scheduled.selectedRun", "Selected run")}{" "}
                    <span className="font-mono text-muted-foreground">
                      {selectedExecution.id.slice(0, 8)}
                    </span>
                  </div>
                  <div className="mt-2 grid gap-1 text-muted-foreground">
                    <div>{t("taskDetail.startedAt", "Started")}: {formatTime(selectedExecution.started_at)}</div>
                    <div>{t("taskDetail.finishedAt", "Finished")}: {formatTime(selectedExecution.finished_at)}</div>
                    <div>{t("tasks.scheduled.notification", "Notification")}: {String(selectedExecution.notification_state?.status ?? "—")}</div>
                  </div>
                </div>
              )}
            </section>
          </div>
        )}

        {/* Summary card (finished only) */}
        {summary && (
          <div className="rounded-md border p-4">
            <h2 className="mb-3 text-sm font-medium">
              {t("taskDetail.summary", "Summary")}
            </h2>
            <dl className="grid grid-cols-3 gap-x-6 gap-y-2 text-sm">
              <div className="flex flex-col">
                <dt className="text-xs text-muted-foreground">
                  {t("taskDetail.rowsTotal", "Rows total")}
                </dt>
                <dd className="tabular-nums">{summary.rows_total ?? "—"}</dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-xs text-muted-foreground">
                  {t("taskDetail.rowsOk", "Rows ok")}
                </dt>
                <dd className="tabular-nums text-state-success">
                  {summary.rows_ok ?? "—"}
                </dd>
              </div>
              <div className="flex flex-col">
                <dt className="text-xs text-muted-foreground">
                  {t("taskDetail.rowsFailed", "Rows failed")}
                </dt>
                <dd className="tabular-nums text-destructive">
                  {summary.rows_failed ?? "—"}
                </dd>
              </div>
            </dl>
          </div>
        )}

        {/* Error block (failed only) */}
        {task.status === "failed" && task.error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/10 p-4">
            <h2 className="mb-2 text-sm font-medium text-destructive">
              {t("taskDetail.error", "Error")}
            </h2>
            <pre className="whitespace-pre-wrap break-all font-mono text-xs text-destructive">
              {task.error}
            </pre>
          </div>
        )}

        {/* Live event log */}
        <div className="rounded-md border p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-medium">
              {t("taskDetail.events", "Events")}
            </h2>
            <span className="text-xs text-muted-foreground">
              {stream.done
                ? t("taskDetail.streamClosed", "Stream closed")
                : t("taskDetail.streamLive", "Live")}
            </span>
          </div>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            {LEVEL_OPTIONS.map((level) => (
              <button
                key={level}
                type="button"
                onClick={() => setLevelFilter(level)}
                className={`rounded-full border px-2.5 py-1 text-xs ${
                  levelFilter === level
                    ? "border-focus bg-focus text-white"
                    : "border-edge-structural bg-surface-raised text-muted-foreground hover:bg-surface-hover"
                }`}
              >
                {level}
              </button>
            ))}
            <span className="mx-1 h-4 w-px bg-border" />
            {(["all", ...EVENT_TYPE_OPTIONS] as const).map((type) => (
              <button
                key={type}
                type="button"
                onClick={() => setEventTypeFilter(type)}
                className={`rounded-full border px-2.5 py-1 text-xs ${
                  eventTypeFilter === type
                    ? "border-focus bg-focus text-white"
                    : "border-edge-structural bg-surface-raised text-muted-foreground hover:bg-surface-hover"
                }`}
              >
                {type}
              </button>
            ))}
          </div>
          {stream.events.length === 0 ? (
            <div className="rounded border border-dashed p-6 text-center text-xs text-muted-foreground">
              {t("taskDetail.noEvents", "No events yet.")}
            </div>
          ) : (
            <ol className="max-h-96 overflow-auto">
              {visibleEvents.map((frame) => (
                <EventRow
                  key={frame.id}
                  frame={frame}
                  formatTime={formatTime}
                  nowLabel={t("taskDetail.justNow", "Just now")}
                />
              ))}
            </ol>
          )}
        </div>
      <ResourceShareDialog
        open={shareOpen}
        onOpenChange={setShareOpen}
        resourceKind="task"
        resourceId={task.id}
        resourceName={`${t("taskDetail.title", "Task")} ${task.id.slice(0, 8)}`}
        effectiveRole={task.access?.effective_role}
        accessSource={task.access?.source}
      />
    </EntityDetailShell>
  );
}
