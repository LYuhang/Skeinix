import { beforeEach, describe, expect, it } from 'vitest';
import type { fetchEventSource } from '@microsoft/fetch-event-source';

import { resumeActiveTurn } from '@/lib/api/sse/resume-turn';
import { useChatStreamStore } from '@/stores/chat-stream';
import {
  releaseTurnStream,
  tryAcquireTurnStream,
} from '@/lib/api/sse/turn-stream-coordinator';

describe('resumeActiveTurn HITL projection', () => {
  beforeEach(() => {
    localStorage.clear();
    useChatStreamStore.getState().reset();
  });

  it('restores the durable active-Turn user message before replaying events', async () => {
    const stream: typeof fetchEventSource = async (_url, opts) => {
      await opts.onopen?.(new Response('', { status: 200 }));
      const runtime = useChatStreamStore.getState().runtimes.chat_input;
      expect(runtime.messages).toEqual([
        expect.objectContaining({
          role: 'user',
          content: 'Approve the browser click',
        }),
      ]);
      opts.onmessage?.({
        id: '1',
        event: 'done',
        data: JSON.stringify({ ok: true }),
      });
    };

    await expect(resumeActiveTurn({
      wfId: 'scope_input',
      chatId: 'chat_input',
      turnId: 'turn_input',
      inputMessage: {
        id: 'chat_input:user:turn_input',
        role: 'user',
        content: 'Approve the browser click',
        attachments: [],
      },
    }, stream)).resolves.toBe(true);
  });

  it('restores waiting-for-user state without pretending the Runtime is thinking', async () => {
    const stream: typeof fetchEventSource = async (_url, opts) => {
      await opts.onopen?.(new Response('', { status: 200 }));
      const runtime = useChatStreamStore.getState().runtimes.chat_waiting;
      expect(runtime.state).toBe('streaming');
      expect(runtime.waitingForUser).toBe(true);
      opts.onmessage?.({
        id: '1',
        event: 'done',
        data: JSON.stringify({ ok: true }),
      });
    };

    await expect(resumeActiveTurn({
      wfId: 'scope_waiting',
      chatId: 'chat_waiting',
      turnId: 'turn_waiting',
      status: 'waiting_approval',
    }, stream)).resolves.toBe(true);
  });

  it('does not overwrite later tool events with the initial pending projection', async () => {
    const stream: typeof fetchEventSource = async (_url, opts) => {
      await opts.onopen?.(new Response('', { status: 200 }));
      opts.onmessage?.({
        id: '1',
        event: 'CHAT_EVENT',
        data: JSON.stringify({
          type: 'tool_start',
          message_id: 'assistant_1',
          tool_call_id: 'call_approval',
          name: 'browser_click',
        }),
      });
      opts.onmessage?.({
        id: '2',
        event: 'CHAT_EVENT',
        data: JSON.stringify({
          type: 'tool_end',
          tool_call_id: 'call_approval',
          content: 'Clicked.',
          status: 'done',
        }),
      });
      opts.onmessage?.({
        id: '3',
        event: 'done',
        data: JSON.stringify({ ok: true }),
      });
    };

    const resumed = await resumeActiveTurn({
      wfId: 'scope_1',
      chatId: 'chat_1',
      turnId: 'turn_1',
      pendingHitl: [{
        hitlRequestId: 'hitl_1',
        hitlType: 'pre_tool_approval',
        status: 'pending',
        uiProjectionEvent: {
          type: 'tool_update',
          tool_call_id: 'call_approval',
          status: 'running',
          artifact: { meta: { pending_approval: true } },
        },
      }],
    }, stream);

    expect(resumed).toBe(true);
    const runtime = useChatStreamStore.getState().runtimes.chat_1;
    const call = runtime.messages.flatMap((message) => message.tool_calls)
      .find((item) => item.id === 'call_approval');
    expect(call?.status).toBe('done');
    expect(call?.result).toBe('Clicked.');
  });

  it('deduplicates concurrent reconciliation streams for the same run', async () => {
    let transportCalls = 0;
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const stream: typeof fetchEventSource = async (_url, opts) => {
      transportCalls += 1;
      await opts.onopen?.(new Response('', { status: 200 }));
      await held;
      opts.onmessage?.({
        id: '1',
        event: 'done',
        data: JSON.stringify({ ok: true }),
      });
    };
    const turn = {
      wfId: 'scope_dedupe',
      chatId: 'chat_dedupe',
      turnId: 'turn_dedupe',
    };

    const first = resumeActiveTurn(turn, stream);
    const second = resumeActiveTurn(turn, stream);
    await Promise.resolve();
    expect(transportCalls).toBe(1);

    release();
    await expect(Promise.all([first, second])).resolves.toEqual([true, true]);
  });

  it('does not open a replay stream while the submission stream owns the chat', async () => {
    const lease = tryAcquireTurnStream(
      'scope_submission',
      'chat_submission',
      'submission',
    );
    expect(lease).not.toBeNull();

    let replayTransportCalls = 0;
    const stream: typeof fetchEventSource = async () => {
      replayTransportCalls += 1;
    };
    const turn = {
      wfId: 'scope_submission',
      chatId: 'chat_submission',
      turnId: 'turn_submission',
    };

    await expect(resumeActiveTurn(turn, stream)).resolves.toBe(true);
    expect(replayTransportCalls).toBe(0);

    releaseTurnStream(lease!);
    const terminalStream: typeof fetchEventSource = async (_url, opts) => {
      replayTransportCalls += 1;
      await opts.onopen?.(new Response('', { status: 200 }));
      opts.onmessage?.({
        id: '1',
        event: 'done',
        data: JSON.stringify({ ok: true }),
      });
    };
    await expect(resumeActiveTurn(turn, terminalStream)).resolves.toBe(true);
    expect(replayTransportCalls).toBe(1);
  });
});
