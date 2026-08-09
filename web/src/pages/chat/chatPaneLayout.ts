import type { Layout as ResizableLayout } from 'react-resizable-panels';

export const CHAT_PANE_MIN_WIDTH = '380px';
export const PREVIEW_PANE_MIN_WIDTH = '400px';
export const WORKFLOW_PREVIEW_PANE_MIN_WIDTH = '600px';
export const DEBUG_PANE_MIN_WIDTH = '420px';

const STORAGE_PREFIX = 'vibecanvas:chat-pane-layout:v1';

export function defaultChatPaneLayout(
  previewVisible: boolean,
  debugVisible: boolean,
): ResizableLayout {
  if (previewVisible && debugVisible) {
    return { chat: 40, preview: 30, debug: 30 };
  }
  if (previewVisible) return { chat: 60, preview: 40 };
  if (debugVisible) return { chat: 54, debug: 46 };
  return { chat: 100 };
}

export function chatPaneLayoutStorageKey(
  scopeKey: string,
  previewVisible: boolean,
  debugVisible: boolean,
): string {
  return `${STORAGE_PREFIX}:${scopeKey}:${previewVisible ? 'view' : 'no-view'}:${debugVisible ? 'debug' : 'no-debug'}`;
}

export function loadChatPaneLayout(
  scopeKey: string,
  previewVisible: boolean,
  debugVisible: boolean,
): ResizableLayout {
  const fallback = defaultChatPaneLayout(previewVisible, debugVisible);
  if (typeof window === 'undefined') return fallback;
  try {
    const value = window.localStorage.getItem(
      chatPaneLayoutStorageKey(scopeKey, previewVisible, debugVisible),
    );
    if (!value) return fallback;
    const parsed = JSON.parse(value) as Record<string, unknown>;
    const expectedIds = Object.keys(fallback);
    const sizes = expectedIds.map((id) => parsed[id]);
    if (
      Object.keys(parsed).length !== expectedIds.length ||
      sizes.some((size) => typeof size !== 'number' || !Number.isFinite(size) || size <= 0) ||
      Math.abs(sizes.reduce<number>((sum, size) => sum + (size as number), 0) - 100) > 0.5
    ) {
      return fallback;
    }
    return Object.fromEntries(expectedIds.map((id) => [id, parsed[id] as number]));
  } catch {
    return fallback;
  }
}

export function saveChatPaneLayout(
  scopeKey: string,
  previewVisible: boolean,
  debugVisible: boolean,
  layout: ResizableLayout,
): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(
      chatPaneLayoutStorageKey(scopeKey, previewVisible, debugVisible),
      JSON.stringify(layout),
    );
  } catch {
    // Layout persistence is a device-local preference; resizing remains usable
    // when storage is disabled or full.
  }
}
