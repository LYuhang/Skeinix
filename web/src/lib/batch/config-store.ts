/**
 * Per-workflow batch INFERENCE config persistence (localStorage).
 *
 * Remembers the user's last batch *configuration* — output columns, output
 * location (+ sheet), and parallel-rows — keyed by workflow id, so re-entering
 * the Batch tab (or reloading) restores it instead of starting blank. The DATA
 * SOURCE (uploaded file / parsed rows / column mapping) is deliberately NOT
 * persisted: that's per-run input, cleared after each submit.
 *
 * Fail-soft: private mode / quota / disabled storage just means no persistence.
 */
import type { BatchColumnsState } from '@/pages/canvas/inspector/batch-output-columns-model';

export interface BatchConfig {
  outputPath: string;
  outputSheet: string;
  concurrency: number;
  columns: BatchColumnsState;
}

const key = (wfId: string) => `vibecanvas:batch-config:${wfId}`;

export function loadBatchConfig(wfId: string): Partial<BatchConfig> | null {
  try {
    const raw = localStorage.getItem(key(wfId));
    if (!raw) return null;
    const value = JSON.parse(raw);
    return value && typeof value === 'object' ? (value as Partial<BatchConfig>) : null;
  } catch {
    return null;
  }
}

export function saveBatchConfig(wfId: string, cfg: BatchConfig): void {
  try {
    localStorage.setItem(key(wfId), JSON.stringify(cfg));
  } catch {
    // ignore — persistence is best-effort.
  }
}
