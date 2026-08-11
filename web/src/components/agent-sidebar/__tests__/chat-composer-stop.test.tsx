import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { ChatComposer } from '@/components/agent-sidebar/ChatComposer';
import { useChatStreamStore } from '@/stores/chat-stream';
import { useAgentSettingsStore } from '@/stores/agent-settings';
import { useChatAgentSettingsStore } from '@/stores/chat-agent-settings';
import { useAuthStore } from '@/stores/auth';
import { chatClientStateKey } from '@/lib/chat/state-key';
import { server } from '@/__tests__/msw-handlers';

function composerKey(chatId: string): string {
  return chatClientStateKey({
    account: useAuthStore.getState().user,
    scopeId: 'wf_x',
    surface: 'chat',
    chatId,
  });
}

function renderComposer(
  chatId: string,
  showModelSelector = false,
  onSendStart?: () => void,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ChatComposer
          wfId="wf_x"
          chatId={chatId}
          showModelSelector={showModelSelector}
          onSendStart={onSendStart}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ChatComposer Stop', () => {
  let cancelled: Array<{ chatId: string; turnId: string }>;

  beforeEach(() => {
    cancelled = [];
    server.use(
      http.get('*/api/v1/chats/bootstrap', () => HttpResponse.json({
        carrier_scope_id: 'wf_x',
        surface: 'chat',
        available_commands: [],
        debug_view_enabled: false,
      })),
      http.post('*/api/v1/chats/:chatId/active-turn/cancel', ({ params }) => {
        cancelled.push({
          chatId: String(params.chatId),
          turnId: 'turn_a',
        });
        return HttpResponse.json(
          {
            status: 'cancel-requested',
            chat_id: String(params.chatId),
            run_id: 'turn_a',
          },
          { status: 202 },
        );
      }),
    );
    useChatStreamStore.getState().reset();
    useAgentSettingsStore.getState().reset();
    useChatAgentSettingsStore.setState({ entries: {} });
  });

  it('shows and stores the concrete API selection instead of a synthetic Default', async () => {
    const modelId = 'langchain:credential:11111111-1111-4111-8111-111111111111';
    server.use(
      http.get('*/api/v1/agent-runtime/capabilities', () => HttpResponse.json({
        protocol_version: 2,
        runtime_type: 'langchain',
        runtime_available: true,
        authenticated: true,
        source: 'test-explicit-api',
        models: [{
          id: modelId,
          label: 'Test account API',
          description: 'openai · gpt-test',
          provider: 'openai',
          is_default: false,
          supported_reasoning_efforts: [],
          default_reasoning_effort: null,
        }],
        default_model_id: modelId,
        error_code: null,
        chat_configuration_locked: false,
        bound_agent_settings: null,
      })),
    );

    const { container } = renderComposer('chat_explicit_api', true);

    await waitFor(() => {
      expect(container.querySelector('[data-role="chat-model-select"]')).toHaveTextContent(
        'Test account API',
      );
      expect(
        useChatAgentSettingsStore.getState().entries.chat_explicit_api?.settings.modelId,
      ).toBe(modelId);
    });
    expect(screen.queryByText(/Runtime default/i)).toBeNull();
  });

  it('preserves an unavailable explicit API instead of switching to another one', async () => {
    const unavailableId = 'langchain:credential:22222222-2222-4222-8222-222222222222';
    const otherId = 'langchain:credential:33333333-3333-4333-8333-333333333333';
    useAgentSettingsStore.getState().set({ modelId: unavailableId });
    server.use(
      http.get('*/api/v1/agent-runtime/capabilities', () => HttpResponse.json({
        protocol_version: 2,
        runtime_type: 'langchain',
        runtime_available: true,
        authenticated: true,
        source: 'test-explicit-api',
        models: [{
          id: otherId,
          label: 'Different API',
          description: 'openai · gpt-other',
          provider: 'openai',
          is_default: false,
          supported_reasoning_efforts: [],
          default_reasoning_effort: null,
        }],
        default_model_id: otherId,
        error_code: null,
        chat_configuration_locked: false,
        bound_agent_settings: null,
      })),
    );

    renderComposer('chat_stale_api', true);

    await waitFor(() => {
      expect(
        useChatAgentSettingsStore.getState().entries.chat_stale_api?.settings.modelId,
      ).toBe(unavailableId);
    });
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
    expect(useChatAgentSettingsStore.getState().entries.chat_stale_api?.settings.modelId)
      .not.toBe(otherId);
  });

  it('clears a model selection inherited from a different Runtime for a new draft', async () => {
    const langChainModelId = 'langchain:credential:44444444-4444-4444-8444-444444444444';
    useAgentSettingsStore.getState().set({ modelId: langChainModelId });
    server.use(
      http.get('*/api/v1/agent-runtime/capabilities', () => HttpResponse.json({
        protocol_version: 2,
        runtime_type: 'codex',
        runtime_available: true,
        authenticated: false,
        source: 'test-runtime-isolation',
        models: [],
        default_model_id: null,
        error_code: 'connection_required',
        chat_configuration_locked: false,
        bound_agent_settings: null,
      })),
    );

    const { container } = renderComposer('chat_cross_runtime_default', true);

    await waitFor(() => {
      expect(
        useChatAgentSettingsStore.getState().entries.chat_cross_runtime_default?.settings.modelId,
      ).toBeNull();
    });
    expect(container.querySelector('[data-role="chat-model-select"]'))
      .toHaveTextContent('No model configured');
    expect(screen.queryByText(new RegExp(langChainModelId))).toBeNull();
  });

  it('waits for the persisted chat row before reading chat state', async () => {
    let stateReads = 0;
    server.use(
      http.get('*/api/v1/chat-scopes/wf_x/chats/chat_pending/state', () => {
        stateReads += 1;
        return HttpResponse.json({
          todo_items: [],
          background_jobs: [],
          active_modes: [],
          mcp_server_ids: [],
          mcp_config_revision: 0,
        });
      }),
    );
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ChatComposer wfId="wf_x" chatId="chat_pending" chatStateReady={false} />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(stateReads).toBe(0);

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ChatComposer wfId="wf_x" chatId="chat_pending" chatStateReady />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(stateReads).toBe(1));
  });

  it('keeps MCP selection available before a draft chat state is materialized', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <ChatComposer
            wfId="wf_x"
            chatId="chat_draft"
            chatStateReady={false}
            showModelSelector
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await userEvent.click(
      container.querySelector('[data-role="chat-composer-options-toggle"]') as HTMLElement,
    );
    await waitFor(() => {
      expect(container.querySelector('[data-role="chat-mcp-picker"]')).toBeEnabled();
    });
  });

  it('does not advertise or resend an uninstalled MCP selection', async () => {
    server.use(
      http.get('*/api/v1/chat-scopes/wf_x/chats/chat_uninstalled_mcp/state', () =>
        HttpResponse.json({
          todo_items: [],
          background_jobs: [],
          active_modes: [],
          mcp_server_ids: ['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'],
          mcp_config_revision: 4,
        })),
      http.get('*/api/v1/mcp-servers', () => HttpResponse.json({ items: [] })),
    );

    const { container } = renderComposer('chat_uninstalled_mcp', true);
    await userEvent.click(
      container.querySelector('[data-role="chat-composer-options-toggle"]') as HTMLElement,
    );

    await waitFor(() => {
      expect(container.querySelector('[data-role="chat-mcp-picker"]')).toHaveTextContent(/^MCP$/);
    });
    expect(container.querySelector('[data-role="chat-composer-options-toggle"]'))
      .not.toHaveTextContent('1');
  });

  it('sends a chat-scoped backend cancel request without aborting or rewriting messages', async () => {
    const abort = vi.fn();
    useChatStreamStore.setState({
      chatId: 'chat_a',
      turnId: 'turn_a',
      state: 'streaming',
      abortController: { abort } as unknown as AbortController,
      buffer: [
        { role: 'user', content: 'run it' },
        {
          role: 'assistant',
          content: '',
          tool_calls: [{ id: 'tc1', name: 'bash', arguments: '{}' }],
        },
      ] as never,
    });

    renderComposer('chat_a');

    await userEvent.click(screen.getByRole('button', { name: /stop|停止/i }));

    await waitFor(() => {
      expect(cancelled).toEqual([{ chatId: 'chat_a', turnId: 'turn_a' }]);
    });
    expect(abort).not.toHaveBeenCalled();
    const s = useChatStreamStore.getState();
    expect(s.state).toBe('streaming');
    expect(s.buffer).toHaveLength(2);
  });

  it('offers Retry after the backend confirms a cancelled turn', () => {
    useChatStreamStore.getState().beginTurn('chat_cancelled', 'turn_cancelled');
    useChatStreamStore.getState().setLastInput({ content: 'run it again' }, 'chat_cancelled');
    useChatStreamStore.getState().setState('cancelled', 'chat_cancelled');

    renderComposer('chat_cancelled');

    expect(screen.getByRole('button', { name: /retry|重试/i })).toBeEnabled();
    expect(screen.queryByRole('button', { name: /stop|停止/i })).toBeNull();
  });

  it('resolves the Run from the backend when the local projection has no Turn id', async () => {
    useChatStreamStore.setState({
      runtimes: {
        chat_a: {
          chatId: 'chat_a',
          turnId: null,
          projectionActive: true,
          state: 'streaming',
          buffer: [],
          messages: [],
          todoItems: null,
          abortController: null,
          lastInput: null,
          startupPhase: null,
          startupProgress: null,
          waitingForUser: false,
        },
      },
    });

    renderComposer('chat_a');

    await userEvent.click(screen.getByRole('button', { name: /stop|停止/i }));

    await waitFor(() => {
      expect(cancelled).toEqual([{ chatId: 'chat_a', turnId: 'turn_a' }]);
    });
    expect(useChatStreamStore.getState().runtimes.chat_a.state).toBe('streaming');
  });

  it('does not expose another chat turn as this composer Stop action', async () => {
    useChatStreamStore.setState({
      chatId: 'chat_a',
      turnId: 'turn_a',
      state: 'streaming',
    });

    renderComposer('chat_b');

    const input = screen.getByRole('textbox');
    await userEvent.type(input, 'hello from b');

    expect(screen.queryByRole('button', { name: /stop|停止/i })).toBeNull();
    expect(screen.getByRole('button', { name: /send|发送/i })).toBeEnabled();
  });

  it('moves text and attachments from the composer into the user message before server acceptance', async () => {
    let releaseRequest: (() => void) | undefined;
    const requestGate = new Promise<void>((resolve) => {
      releaseRequest = resolve;
    });
    server.use(
      http.get('*/api/v1/chat-scopes/wf_x/chats/chat_optimistic/state', () =>
        HttpResponse.json({
          todo_items: [],
          background_jobs: [],
          active_modes: [],
          mcp_server_ids: [],
          mcp_config_revision: 0,
        })),
      http.post(
        '*/api/v1/chat-scopes/wf_x/chats/chat_optimistic/messages',
        async () => {
          await requestGate;
          return new HttpResponse('id: 1\nevent: done\ndata: {}\n\n', {
            headers: {
              'Content-Type': 'text/event-stream',
              'X-Turn-Id': 'turn_optimistic',
            },
          });
        },
      ),
    );

    const attachment = {
      type: 'file' as const,
      name: 'customer-feedback.csv',
      path: '/data/attachments/customer-feedback.csv',
      content_type: 'text/csv',
      size_bytes: 128,
    };
    const stateKey = composerKey('chat_optimistic');
    useChatStreamStore.getState().addAttachment(stateKey, attachment);
    const onSendStart = vi.fn();
    const { container } = renderComposer('chat_optimistic', false, onSendStart);
    const input = screen.getByRole('textbox');
    expect(container.querySelectorAll('[data-role="agent-composer-attachment-chip"]'))
      .toHaveLength(1);
    await userEvent.type(input, 'show this immediately');
    await userEvent.click(screen.getByRole('button', { name: /send|发送/i }));

    // The page must switch from its empty shell to the transcript while the
    // request is still blocked, so network setup time is represented by the
    // optimistic user bubble and Agent thinking state.
    expect(onSendStart).toHaveBeenCalledTimes(1);
    expect(input).toHaveValue('');
    expect(container.querySelectorAll('[data-role="agent-composer-attachment-chip"]'))
      .toHaveLength(0);
    expect(useChatStreamStore.getState().pendingAttachments[stateKey])
      .toBeUndefined();
    expect(useChatStreamStore.getState().runtimes.chat_optimistic.buffer).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          role: 'user',
          content: 'show this immediately',
          attachments: [attachment],
        }),
      ]),
    );
    expect(useChatStreamStore.getState().runtimes.chat_optimistic.messages[0])
      .toMatchObject({
        role: 'user',
        content: 'show this immediately',
        attachments: [attachment],
      });

    releaseRequest?.();
    await waitFor(() => {
      expect(useChatStreamStore.getState().runtimes.chat_optimistic.turnId)
        .toBe('turn_optimistic');
    });
  });

  it('restores text and attachments when the backend rejects the turn before acceptance', async () => {
    server.use(
      http.get('*/api/v1/chat-scopes/wf_x/chats/chat_rejected/state', () =>
        HttpResponse.json({
          todo_items: [],
          background_jobs: [],
          active_modes: [],
          mcp_server_ids: [],
          mcp_config_revision: 0,
        })),
      http.post(
        '*/api/v1/chat-scopes/wf_x/chats/chat_rejected/messages',
        () => HttpResponse.json(
          { detail: { code: 'chat_run_active' } },
          { status: 409 },
        ),
      ),
      http.get(
        '*/api/v1/chats/chat_rejected/turns/by-client-request/:clientRequestId',
        () => HttpResponse.json({ detail: 'not found' }, { status: 404 }),
      ),
    );
    const attachment = {
      type: 'file' as const,
      name: 'support-playbook.md',
      path: '/data/attachments/support-playbook.md',
      content_type: 'text/markdown',
      size_bytes: 256,
    };
    const stateKey = composerKey('chat_rejected');
    useChatStreamStore.getState().addAttachment(stateKey, attachment);

    const { container } = renderComposer('chat_rejected');
    const input = screen.getByRole('textbox');
    await userEvent.type(input, 'keep this draft');
    await userEvent.click(screen.getByRole('button', { name: /send|发送/i }));

    await waitFor(() => {
      expect(input).toHaveValue('keep this draft');
      expect(container.querySelectorAll('[data-role="agent-composer-attachment-chip"]'))
        .toHaveLength(1);
    });
    expect(useChatStreamStore.getState().pendingAttachments[stateKey])
      .toEqual([attachment]);
  });

  it('keeps inline controls transparent while a turn disables them', async () => {
    useChatStreamStore.getState().beginTurn('chat_a', 'turn_a');

    const { container } = renderComposer('chat_a', true);

    await waitFor(() => {
      expect(container.querySelector('[data-role="chat-model-select"]')).toBeDisabled();
    });
    expect(container.querySelector('[data-role="chat-approval-mode-select"]')).toBeNull();
    for (const role of [
      'chat-model-select',
      'chat-reasoning-effort-select',
    ]) {
      const trigger = container.querySelector(`[data-role="${role}"]`);
      if (!trigger) continue;
      expect(trigger).toHaveClass('disabled:bg-transparent');
      expect(trigger).toBeDisabled();
    }
  });

  it('does not render active commands as composer attachment chips', async () => {
    server.use(
      http.get('*/api/v1/chats/bootstrap', () => HttpResponse.json({
        carrier_scope_id: 'wf_x',
        surface: 'chat',
        available_commands: ['plan', 'knowledge'],
      })),
      http.get('*/api/v1/chat-scopes/wf_x/chats/chat_codex/state', () => HttpResponse.json({
        todo_items: [],
        background_jobs: [],
        active_modes: ['plan'],
        mcp_server_ids: [],
        mcp_config_revision: 0,
      })),
      http.get('*/api/v1/agent-runtime/capabilities', () => HttpResponse.json({
        protocol_version: 1,
        runtime_type: 'codex',
        runtime_available: true,
        authenticated: true,
        source: 'chat-binding',
        models: [],
        default_model_id: null,
        chat_configuration_locked: false,
        bound_agent_settings: null,
        error_code: null,
      })),
    );

    const { container } = renderComposer('chat_codex', true);

    await waitFor(() => expect(screen.getByRole('textbox')).toBeEnabled());
    expect(container.querySelector('[data-role="active-command-list"]')).not.toBeInTheDocument();
    expect(container.querySelector('[data-command="plan"]')).not.toBeInTheDocument();
  });

});
