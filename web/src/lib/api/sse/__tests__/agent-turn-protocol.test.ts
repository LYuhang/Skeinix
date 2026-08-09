import { beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/__tests__/msw-handlers';

let capturedBody: Record<string, unknown> | null = null;

import { streamAgentTurn } from '@/lib/api/sse/agent-stream';
import { runAgentTurn } from '@/lib/api/sse/run-agent-turn';
import { useChatStreamStore } from '@/stores/chat-stream';

describe('Agent Turn wire protocol', () => {
  beforeEach(() => {
    capturedBody = null;
    localStorage.clear();
    useChatStreamStore.getState().reset();
    server.use(
      http.post('*/api/v1/chat-scopes/:scopeId/chats/:chatId/messages', async ({ request }) => {
        capturedBody = await request.json() as Record<string, unknown>;
        return new HttpResponse('id: 1\nevent: done\ndata: {}\n\n', {
          status: 200,
          headers: {
            'Content-Type': 'text/event-stream',
            'X-Turn-Id': 'turn_protocol_1',
          },
        });
      }),
    );
  });

  it('auto-approves tools and sends surface but no browser topology', async () => {
    await streamAgentTurn({
      wfId: 'scope_1',
      chatId: 'chat_1',
      content: 'read the current page',
      mode: 'browser',
      // The field is retained as a future extension seam, but the official
      // client currently normalizes every request to automatic approval.
      approvalMode: 'always_ask',
      surface: 'sidepanel',
      agentSurface: 'browser',
      signal: new AbortController().signal,
    });

    expect(capturedBody).toMatchObject({
      role: 'user',
      content: 'read the current page',
      mode: 'browser',
      approval_mode: 'always_allow',
      surface: 'sidepanel',
      agent_surface: 'browser',
    });
    expect(capturedBody).not.toHaveProperty('browser');
    expect(capturedBody).not.toHaveProperty('browser_id');
    expect(capturedBody).not.toHaveProperty('browser_window_id');
    expect(capturedBody).not.toHaveProperty('browser_panel_context_id');
    expect(capturedBody).not.toHaveProperty('client_context_id');
  });

  it('uses the durable HITL id as the Continue Turn idempotency key', async () => {
    const onAccepted = vi.fn();
    await streamAgentTurn({
      wfId: 'scope_1',
      chatId: 'chat_1',
      content: '',
      control: {
        type: 'hitl_continue',
        version: 1,
        hitl_request_id: 'hitl_review_1',
        artifact_id: 'ia_review_1',
        action: 'continue',
      },
      onAccepted,
      signal: new AbortController().signal,
    });

    expect(capturedBody).toMatchObject({
      role: 'user',
      content: '',
      client_request_id: 'hitl_continue:hitl_review_1',
      control: {
        type: 'hitl_continue',
        version: 1,
        hitl_request_id: 'hitl_review_1',
        artifact_id: 'ia_review_1',
        action: 'continue',
      },
    });
    expect(onAccepted).toHaveBeenCalledOnce();
  });

  it('forwards the durable acceptance callback through the Turn orchestrator', async () => {
    const onAccepted = vi.fn();
    await runAgentTurn({
      wfId: 'scope_1',
      chatId: 'chat_accepted',
      content: 'hello',
      onAccepted,
    });
    expect(onAccepted).toHaveBeenCalledOnce();
  });

  it('reports a rejected active-turn race without treating it as sent', async () => {
    server.use(
      http.post('*/api/v1/chat-scopes/:scopeId/chats/:chatId/messages', () => (
        HttpResponse.json(
          { detail: { code: 'chat_run_active' } },
          { status: 409 },
        )
      )),
    );
    const onAccepted = vi.fn();
    const sent = await runAgentTurn({
      wfId: 'scope_1',
      chatId: 'chat_busy',
      content: 'keep this draft',
      onAccepted,
    });
    expect(sent).toBe(false);
    expect(onAccepted).not.toHaveBeenCalled();
  });

  it('detects a missing frame and resumes from the last projected sequence', async () => {
    let resumeCursor = '';
    server.use(
      http.post('*/api/v1/chat-scopes/:scopeId/chats/:chatId/messages', () => (
        new HttpResponse(
          [
            'id: 1\nevent: started\ndata: {"turn_id":"turn_gap_1"}\n\n',
            // seq=2 is deliberately absent from this connection.
            'id: 3\nevent: CHAT_EVENT\ndata: {"type":"message_delta","message_id":"assistant_gap","delta":"complete"}\n\n',
          ].join(''),
          {
            status: 200,
            headers: {
              'Content-Type': 'text/event-stream',
              'X-Turn-Id': 'turn_gap_1',
            },
          },
        )
      )),
      http.get('*/api/v1/chats/chat_gap/turns/turn_gap_1/stream', ({ request }) => {
        resumeCursor = request.headers.get('Last-Event-ID') ?? '';
        return new HttpResponse(
          [
            'id: 2\nevent: CHAT_EVENT\ndata: {"type":"message_start","message_id":"assistant_gap","role":"assistant"}\n\n',
            'id: 3\nevent: CHAT_EVENT\ndata: {"type":"message_delta","message_id":"assistant_gap","delta":"complete"}\n\n',
            'id: 4\nevent: done\ndata: {}\n\n',
          ].join(''),
          {
            status: 200,
            headers: { 'Content-Type': 'text/event-stream' },
          },
        );
      }),
    );
    useChatStreamStore.getState().beginTurn('chat_gap', '');

    await streamAgentTurn({
      wfId: 'scope_gap',
      chatId: 'chat_gap',
      content: 'test continuity',
      signal: new AbortController().signal,
    });

    expect(resumeCursor).toBe('1');
    expect(useChatStreamStore.getState().runtimes.chat_gap.messages).toEqual([
      expect.objectContaining({
        id: 'assistant_gap',
        content: 'complete',
      }),
    ]);
  });
});
