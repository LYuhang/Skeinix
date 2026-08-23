import { forwardRef, useImperativeHandle, type ComponentProps } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import type { ChatPreviewItem } from '@/lib/chat/preview-state';
import { ChatPreviewPane } from '../ChatPreviewPane';

const fileViewer = vi.hoisted(() => ({
  requestLeave: vi.fn<(onLeave: () => void) => void>(),
}));

vi.mock('../preview/ChatFilePreview', () => ({
  ChatFilePreview: forwardRef(function MockChatFilePreview(_props, ref) {
    useImperativeHandle(ref, () => ({ requestLeave: fileViewer.requestLeave }));
    return <div>Active file</div>;
  }),
}));

vi.mock('../ChatWorkflowViewer', () => ({
  ChatWorkflowViewer: () => <div>Workflow preview</div>,
}));

const items: ChatPreviewItem[] = [
  {
    id: 'file-1',
    title: 'notes.md',
    resource: {
      schemaVersion: 1,
      kind: 'file',
      fileRef: { schemaVersion: 1, scope: 'chat', chatId: 'chat-1', path: '/data/notes.md' },
    },
  },
  {
    id: 'workflow-1',
    title: 'Review workflow',
    resource: { schemaVersion: 1, kind: 'workflow', workflowId: 'wf-1' },
  },
];

function renderPane(overrides: Partial<ComponentProps<typeof ChatPreviewPane>> = {}) {
  const props: ComponentProps<typeof ChatPreviewPane> = {
    scopeId: 'scope-1',
    open: true,
    items,
    resources: items,
    activeId: 'file-1',
    onToggleOpen: vi.fn(),
    onSelect: vi.fn(),
    onOpenResource: vi.fn(),
    onCloseItem: vi.fn(),
    ...overrides,
  };
  render(<ChatPreviewPane {...props} />);
  return props;
}

describe('ChatPreviewPane dirty-file leave protocol', () => {
  it('defers tab selection until the active file viewer allows leaving', async () => {
    fileViewer.requestLeave.mockReset();
    const props = renderPane();
    await screen.findByText('Active file');

    fireEvent.click(screen.getByRole('button', { name: 'Review workflow' }));
    expect(fileViewer.requestLeave).toHaveBeenCalledOnce();
    expect(props.onSelect).not.toHaveBeenCalled();

    fileViewer.requestLeave.mock.calls[0]?.[0]();
    expect(props.onSelect).toHaveBeenCalledWith('workflow-1');
  });

  it('uses the same guard before closing the whole preview pane', async () => {
    fileViewer.requestLeave.mockReset();
    const props = renderPane();
    await screen.findByText('Active file');

    fireEvent.click(screen.getByRole('button', { name: /^close preview$/i }));
    expect(fileViewer.requestLeave).toHaveBeenCalledOnce();
    expect(props.onToggleOpen).not.toHaveBeenCalled();
  });

  it('constrains long file content to the Preview viewport', async () => {
    renderPane();
    await screen.findByText('Active file');

    expect(document.querySelector('[data-role="chat-preview-content"]')).toHaveClass(
      'min-h-0',
      'overflow-hidden',
    );
  });
});
