/**
 * `TaskDetailPage` smoke test.
 *
 * Mocks the two data sources independently so the test can exercise
 * the page's three render branches in isolation:
 *   * Loading — neither hook has resolved.
 *   * Running task with live SSE frames — assert the header, progress,
 *     and a frame from the event log are rendered.
 *   * Finished task with a result summary — assert the summary card
 *     surfaces the row counts.
 *
 * Why module-mock both `@/lib/api/tasks` (REST) and
 * `@/lib/api/sse/run-task-stream` (SSE): the page composes the two
 * channels — polling for the canonical row + streaming for the live
 * event log — and the unit test should not actually open a network
 * connection or a real SSE channel. The list-page test (T14) follows
 * the same module-mock pattern.
 */
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { I18nextProvider, initReactI18next } from "react-i18next";
import i18n from "i18next";

import type { Task, TaskStatus } from "@/lib/api/tasks";

vi.mock("@/lib/api/tasks", () => ({
  getTask: vi.fn(),
  cancelTask: vi.fn(),
  resumeTask: vi.fn(),
}));

vi.mock("@/lib/api/sse/run-task-stream", () => ({
  useTaskStream: vi.fn(),
}));

import { getTask } from "@/lib/api/tasks";
import { useTaskStream } from "@/lib/api/sse/run-task-stream";
import { TaskDetailPage } from "@/pages/tasks/TaskDetailPage";

const TASK_ID = "00000000-0000-0000-0000-000000000abc";

// Local i18n instance — empty resources mean each `t(key, default)`
// call falls back to the inline default string. Same pattern as the
// list-page test in T14.
const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: "en",
  fallbackLng: "en",
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderAt(taskId: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={testI18n}>
        <MemoryRouter initialEntries={[`/tasks/${taskId}`]}>
          <Routes>
            <Route path="/tasks/:taskId" element={<TaskDetailPage />} />
          </Routes>
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

function makeTask(over: Partial<{
  status: TaskStatus;
  progress: number;
  result: unknown;
  results_uri: string | null;
  error: string | null;
  finished_at: string | null;
}> = {}): Task {
  return {
    id: TASK_ID,
    status: "running" as TaskStatus,
    progress: 0.42,
    task_type: "batch_exec",
    workflow_id: "wf_42",
    payload: {},
    result: null,
    results_uri: null,
    error: null,
    celery_id: TASK_ID,
    submitted_at: "2026-05-24T10:00:00Z",
    started_at: "2026-05-24T10:00:01Z",
    finished_at: null,
    access: { capabilities: ['view', 'export', 'update', 'delete', 'manage_access', 'execute', 'cancel', 'resume', 'inspect_runs'], effective_role: 'manager', source: 'computed' },
    ...over,
  };
}

describe("<TaskDetailPage>", () => {
  beforeEach(() => {
    vi.mocked(useTaskStream).mockReturnValue({ events: [], done: false });
  });

  it("renders the workflow id, status badge, and progress for a running task", async () => {
    vi.mocked(getTask).mockResolvedValue(makeTask());
    vi.mocked(useTaskStream).mockReturnValue({
      events: [
        {
          id: 1,
          event_type: "progress",
          payload: { progress: { done: 1, total: 2 } },
        },
      ],
      done: false,
    });

    renderAt(TASK_ID);

    // workflow id from the task row
    await waitFor(() => {
      expect(screen.getByText(/wf_42/)).toBeInTheDocument();
    });
    // progress (Math.round(0.42 * 100) = 42)
    expect(screen.getByText("42%")).toBeInTheDocument();
    // plain-language row progress derived from the live `progress` SSE frame
    // ({done:1, total:2}) so a non-technical user reads it at a glance.
    expect(screen.getByTestId("row-progress")).toHaveTextContent(
      "1 of 2 rows done",
    );
    // status badge — the page uses `tasks.status.running` with default
    // 'running' as the fallback string
    expect(screen.getByText("running")).toBeInTheDocument();
    // event row — the mocked SSE frame renders its event_type alongside filters.
    expect(screen.getAllByText("progress").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Share task" })).toBeInTheDocument();
  });

  it("shows cleanup and interrupted states as Cancelled without changing progress", async () => {
    vi.mocked(getTask).mockResolvedValue(
      makeTask({ status: "cancelling", progress: 0.42 }),
    );

    renderAt(TASK_ID);

    await waitFor(() => {
      expect(screen.getByText("cancelled")).toBeInTheDocument();
    });
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.queryByText("cancelling")).not.toBeInTheDocument();
  });

  it("renders the summary card when the task has finished with a result", async () => {
    vi.mocked(getTask).mockResolvedValue(
      makeTask({
        status: "finished",
        progress: 1,
        result: { rows_total: 10, rows_ok: 8, rows_failed: 2 },
        results_uri: "memory://tasks/abc/results.csv",
        finished_at: "2026-05-24T10:05:00Z",
      }),
    );

    renderAt(TASK_ID);

    await waitFor(() => {
      expect(screen.getByText("Summary")).toBeInTheDocument();
    });
    // The three summary counters (rows_total / rows_ok / rows_failed)
    expect(screen.getByText("10")).toBeInTheDocument();
    expect(screen.getByText("8")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    // Download link → backend redirect endpoint
    expect(screen.getByRole("button", { name: /download/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /view in storage/i })).toHaveAttribute(
      "href",
      `/storage?path=${encodeURIComponent(`/task/${TASK_ID}`)}`,
    );
  });

  it("shows the load-error fallback when the task cannot be fetched", async () => {
    vi.mocked(getTask).mockRejectedValue(new Error("404"));

    renderAt(TASK_ID);

    await waitFor(() => {
      expect(
        screen.getByText(
          /failed to load this task/i,
        ),
      ).toBeInTheDocument();
    });
  });
});
