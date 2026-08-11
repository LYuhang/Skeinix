import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { http, HttpResponse } from 'msw';
import { ChatComposer } from '@/components/agent-sidebar/ChatComposer';
import { MessageItem } from '@/components/agent-sidebar/MessageItem';
import {
  emphasizeUserText,
  findAttachmentMention,
  insertAttachmentMention,
} from '@/components/agent-sidebar/chat-attachments';
import { useChatStreamStore } from '@/stores/chat-stream';
import { server } from '@/__tests__/msw-handlers';

const SCOPE = '__chat_test';
const CHAT = 'chat_attachment_test';

const { uploadChatAttachmentMock } = vi.hoisted(() => ({
  uploadChatAttachmentMock: vi.fn(),
}));

vi.mock('@/lib/api/queries/chats', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/queries/chats')>();
  return { ...actual, uploadChatAttachment: uploadChatAttachmentMock };
});

function renderComposer() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ChatComposer wfId={SCOPE} chatId={CHAT} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('chat attachments', () => {
  beforeEach(() => {
    localStorage.clear();
    useChatStreamStore.getState().reset();
    uploadChatAttachmentMock.mockReset();
    uploadChatAttachmentMock.mockImplementation(async ({ file, type }) => ({
      type,
      name: file.name,
      path: `/data/attachments/${crypto.randomUUID()}_${file.name}`,
      content_type: file.type,
      size_bytes: file.size,
    }));
    server.use(
      http.get('*/api/v1/chats/bootstrap', () => HttpResponse.json({
        carrier_scope_id: SCOPE,
        surface: 'chat',
        available_commands: [],
        debug_view_enabled: false,
      })),
      http.get('*/api/v1/chat-scopes/:scopeId/chats/:chatId/state', () => HttpResponse.json({
        todo_items: [],
        background_jobs: [],
        active_modes: [],
        mcp_server_ids: [],
        mcp_config_revision: 0,
      })),
    );
  });

  it('uses one upload pipeline for picker, paste, and drag/drop, then completes @ mentions', async () => {
    const { container } = renderComposer();
    const input = screen.getByRole('textbox');
    const fileInput = container.querySelector('[data-role="agent-composer-file-input"]') as HTMLInputElement;
    const pickerFile = new File(['doc'], 'brief.pdf', { type: 'application/pdf' });
    fireEvent.change(fileInput, { target: { files: [pickerFile] } });

    const pastedImage = new File(['img'], 'photo.png', { type: 'image/png' });
    fireEvent.paste(input, { clipboardData: { files: [pastedImage] } });

    const droppedVideo = new File(['vid'], 'clip.mp4', { type: 'video/mp4' });
    const dropzone = container.querySelector('[data-role="agent-composer-dropzone"]') as HTMLElement;
    fireEvent.drop(dropzone, {
      dataTransfer: { files: [droppedVideo], types: ['Files'] },
    });

    await waitFor(() => {
      expect(container.querySelectorAll('[data-role="agent-composer-attachment-chip"]')).toHaveLength(3);
    });
    const uploaded = Object.values(useChatStreamStore.getState().pendingAttachments).flat();
    expect(uploadChatAttachmentMock).toHaveBeenCalledTimes(3);
    expect(uploaded).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: 'brief.pdf', type: 'file' }),
      expect.objectContaining({ name: 'photo.png', type: 'image' }),
      expect.objectContaining({ name: 'clip.mp4', type: 'video' }),
    ]));

    await userEvent.type(input, 'Review@pho');
    const mention = await screen.findByRole('option', { name: /photo\.png/i });
    await userEvent.click(mention);
    expect(input).toHaveValue('Review@photo.png ');
  });

  it('serializes a multi-file picker selection and preserves its attachment order', async () => {
    const { container } = renderComposer();
    const fileInput = container.querySelector('[data-role="agent-composer-file-input"]') as HTMLInputElement;
    const firstFile = new File(['first'], 'first.csv', { type: 'text/csv' });
    const secondFile = new File(['second'], 'second.md', { type: 'text/markdown' });
    let releaseFirst!: (attachment: {
      type: 'file';
      name: string;
      path: string;
      content_type: string;
      size_bytes: number;
    }) => void;

    uploadChatAttachmentMock
      .mockImplementationOnce(() => new Promise((resolve) => {
        releaseFirst = resolve;
      }))
      .mockImplementationOnce(async () => ({
        type: 'file',
        name: secondFile.name,
        path: `/data/attachments/${secondFile.name}`,
        content_type: secondFile.type,
        size_bytes: secondFile.size,
      }));

    fireEvent.change(fileInput, { target: { files: [firstFile, secondFile] } });
    await waitFor(() => expect(uploadChatAttachmentMock).toHaveBeenCalledTimes(1));

    releaseFirst({
      type: 'file',
      name: firstFile.name,
      path: `/data/attachments/${firstFile.name}`,
      content_type: firstFile.type,
      size_bytes: firstFile.size,
    });

    await waitFor(() => {
      expect(uploadChatAttachmentMock).toHaveBeenCalledTimes(2);
      expect(container.querySelectorAll('[data-role="agent-composer-attachment-chip"]')).toHaveLength(2);
    });
    expect(Object.values(useChatStreamStore.getState().pendingAttachments)
      .flat()
      .map((item) => item.name))
      .toEqual(['first.csv', 'second.md']);
  });

  it('renders commands as ordinary text and emphasizes only durable attachments', () => {
    render(
      <MessageItem
        message={{
          role: 'user',
          content: '/build compare @photo.png with @not-attached',
          tool_calls: [],
          attachments: [{
            type: 'image',
            name: 'photo.png',
            path: '/data/attachments/photo.png',
            content_type: 'image/png',
            size_bytes: 3,
          }],
        }}
      />,
    );
    expect(screen.getByText(/\/build compare/)).not.toHaveAttribute('data-token-kind');
    expect(screen.getByText('@photo.png')).toHaveAttribute('data-token-kind', 'attachment');
    expect(screen.getByText(/@not-attached/).tagName).toBe('SPAN');
  });

  it('keeps mention parsing and replacement independent from rendering', () => {
    const query = findAttachmentMention('Look at @rep', 12);
    expect(query).toEqual({ start: 8, end: 12, query: 'rep' });
    expect(insertAttachmentMention('Look at @rep now', query!, 'report 1.csv')).toEqual({
      value: 'Look at @report 1.csv  now',
      caret: 22,
    });
    expect(emphasizeUserText('@random', [])).toEqual([
      { text: '@random', emphasized: false },
    ]);
    const direct = '请查看@pho';
    expect(findAttachmentMention(direct, direct.length)).toEqual({
      start: 3,
      end: direct.length,
      query: 'pho',
    });
  });
});
