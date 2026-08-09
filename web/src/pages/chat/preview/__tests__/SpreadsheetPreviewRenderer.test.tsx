import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { PreviewDescriptorV1 } from '@/lib/preview/protocol';
import { SpreadsheetPreviewRenderer } from '../SpreadsheetPreviewRenderer';
import { workbookCellDisplayText } from '../spreadsheet-cell-text';

vi.mock('ag-grid-community', () => ({
  AllCommunityModule: {},
  ModuleRegistry: { registerModules: vi.fn() },
  themeQuartz: {},
}));
vi.mock('ag-grid-react', () => ({
  AgGridReact: () => null,
}));

const descriptor: PreviewDescriptorV1 = {
  schemaVersion: 1,
  fileRef: {
    schemaVersion: 1,
    scope: 'chat',
    chatId: 'chat-1',
    path: '/data/pending.csv',
  },
  name: 'pending.csv',
  sizeBytes: 128,
  contentType: 'text/csv',
  detectedType: 'csv',
  revision: 'sha256:pending',
  renderer: 'spreadsheet',
  loadPolicy: 'stream',
  capabilities: { preview: true, edit: true, download: true },
  content: {
    url: '/api/v1/previews/content/pending.csv',
    truncated: false,
    rangeSupported: true,
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('SpreadsheetPreviewRenderer lifecycle', () => {
  it('shows a formula when the workbook has no cached calculation result', () => {
    expect(workbookCellDisplayText({
      text: '',
      value: { formula: 'SUM(B5:B10)' },
    })).toBe('=SUM(B5:B10)');
  });

  it('prefers a cached formula result when ExcelJS exposes one as text', () => {
    expect(workbookCellDisplayText({
      text: '$2,192,850.00',
      value: { formula: 'SUM(E5:E10)', result: 2192850 },
    })).toBe('$2,192,850.00');
  });

  it('keeps structured text tables read-only even if a stale descriptor advertises edit', async () => {
    render(
      <SpreadsheetPreviewRenderer
        descriptor={{
          ...descriptor,
          loadPolicy: 'inline',
          content: {
            inlineText: 'name,value\nalpha,1\n',
            truncated: false,
            rangeSupported: false,
          },
        }}
        loadAllowed
        onDirtyChange={vi.fn()}
      />,
    );

    expect(await screen.findByText('Read only')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();
  });

  it('aborts an in-flight file read when its Preview tab is closed', async () => {
    let requestSignal: AbortSignal | undefined;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      requestSignal = init?.signal ?? undefined;
      return new Promise<Response>((_resolve, reject) => {
        requestSignal?.addEventListener(
          'abort',
          () => reject(new DOMException('Aborted', 'AbortError')),
          { once: true },
        );
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    const queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false },
        mutations: { retry: false },
      },
    });

    const view = render(
      <QueryClientProvider client={queryClient}>
        <SpreadsheetPreviewRenderer
          descriptor={descriptor}
          loadAllowed
          onDirtyChange={vi.fn()}
        />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    expect(requestSignal?.aborted).toBe(false);

    view.unmount();

    expect(requestSignal?.aborted).toBe(true);
  });
});
