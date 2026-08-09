import { beforeEach, describe, expect, it } from 'vitest';

import {
  clearActiveTurn,
  readActiveTurnFor,
  rememberActiveTurn,
  updateActiveTurnCursor,
} from '../active-turn';

describe('active-turn frontend projection', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('preserves pending HITL and base checkpoint across cursor updates/reload', () => {
    rememberActiveTurn({
      wfId: 'scope_1',
      chatId: 'chat_1',
      turnId: 'turn_1',
      status: 'waiting_approval',
      pendingHitl: [{
        hitlRequestId: 'hitl_1',
        hitlType: 'pre_tool_approval',
        status: 'pending',
        title: 'Approve browser_click',
        uiProjectionEvent: {
          type: 'tool_update',
          tool_call_id: 'tc_1',
        },
      }],
    });
    updateActiveTurnCursor({
      wfId: 'scope_1',
      chatId: 'chat_1',
      turnId: 'turn_1',
    }, 17);

    const restored = readActiveTurnFor('scope_1', 'chat_1');
    expect(restored?.lastEventId).toBe(17);
    expect(restored?.status).toBe('waiting_approval');
    expect(restored?.pendingHitl).toHaveLength(1);
    expect(restored?.pendingHitl?.[0].hitlRequestId).toBe('hitl_1');

    clearActiveTurn({ wfId: 'scope_1', chatId: 'chat_1' });
    expect(readActiveTurnFor('scope_1', 'chat_1')).toBeNull();
  });
});
