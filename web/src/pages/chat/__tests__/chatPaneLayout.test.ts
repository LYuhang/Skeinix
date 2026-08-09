import { beforeEach, describe, expect, it } from 'vitest';

import {
  WORKFLOW_PREVIEW_PANE_MIN_WIDTH,
  chatPaneLayoutStorageKey,
  defaultChatPaneLayout,
  loadChatPaneLayout,
  saveChatPaneLayout,
} from '../chatPaneLayout';

describe('chat pane layout', () => {
  beforeEach(() => localStorage.clear());

  it('uses stable defaults for every visible pane combination', () => {
    expect(WORKFLOW_PREVIEW_PANE_MIN_WIDTH).toBe('600px');
    expect(defaultChatPaneLayout(false, false)).toEqual({ chat: 100 });
    expect(defaultChatPaneLayout(true, false)).toEqual({ chat: 60, preview: 40 });
    expect(defaultChatPaneLayout(false, true)).toEqual({ chat: 54, debug: 46 });
    expect(defaultChatPaneLayout(true, true)).toEqual({
      chat: 40,
      preview: 30,
      debug: 30,
    });
  });

  it('stores each pane combination independently and restores it', () => {
    saveChatPaneLayout('account-a', true, false, { chat: 52, preview: 48 });
    saveChatPaneLayout('account-a', true, true, { chat: 36, preview: 34, debug: 30 });

    expect(loadChatPaneLayout('account-a', true, false)).toEqual({ chat: 52, preview: 48 });
    expect(loadChatPaneLayout('account-a', true, true)).toEqual({ chat: 36, preview: 34, debug: 30 });
    expect(loadChatPaneLayout('account-a', false, true)).toEqual({ chat: 54, debug: 46 });
  });

  it('rejects malformed, incomplete, and non-normalized persisted layouts', () => {
    const key = chatPaneLayoutStorageKey('account-a', true, true);
    const fallback = { chat: 40, preview: 30, debug: 30 };

    localStorage.setItem(key, '{broken');
    expect(loadChatPaneLayout('account-a', true, true)).toEqual(fallback);

    localStorage.setItem(key, JSON.stringify({ chat: 70, preview: 30 }));
    expect(loadChatPaneLayout('account-a', true, true)).toEqual(fallback);

    localStorage.setItem(key, JSON.stringify({ chat: 50, preview: 40, debug: 20 }));
    expect(loadChatPaneLayout('account-a', true, true)).toEqual(fallback);

    localStorage.setItem(key, JSON.stringify({ chat: 70, preview: 30, debug: 0 }));
    expect(loadChatPaneLayout('account-a', true, true)).toEqual(fallback);
  });
});
