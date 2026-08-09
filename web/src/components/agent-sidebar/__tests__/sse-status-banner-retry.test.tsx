import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { SSEStatusBanner } from '@/components/agent-sidebar/SSEStatusBanner';
import { useChatStreamStore } from '@/stores/chat-stream';

const runAgentTurnMock = vi.fn().mockResolvedValue(undefined);

describe('SSEStatusBanner retry protocol', () => {
  beforeEach(() => {
    runAgentTurnMock.mockClear();
    useChatStreamStore.getState().reset();
  });

  it('replays the complete browser Turn policy and surface snapshot without browser topology', async () => {
    const store = useChatStreamStore.getState();
    store.beginTurn('chat_browser', 'turn_interrupted');
    store.setLastInput({
      content: 'continue on the current page',
      mode: 'browser',
      surface: 'sidepanel',
      agentSurface: 'browser',
      approvalMode: 'always_allow',
    }, 'chat_browser');
    store.setState('interrupted', 'chat_browser');

    render(
      <SSEStatusBanner
        wfId="scope_1"
        activeChatId="chat_browser"
        runTurn={runAgentTurnMock}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: /retry|重试/i }));

    expect(runAgentTurnMock).toHaveBeenCalledWith({
      wfId: 'scope_1',
      chatId: 'chat_browser',
      content: 'continue on the current page',
      attachments: undefined,
      mode: 'browser',
      surface: 'sidepanel',
      agentSurface: 'browser',
      approvalMode: 'always_allow',
    });
  });
});
