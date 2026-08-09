import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';

// IMPORTANT (isolate=false): mock ONLY the `@/lib/api/vfs` client layer — NOT
// `@/lib/api/queries/vfs` — because sections.test.tsx imports the real
// useVfsList/useUploadVfsFile hooks from queries/vfs. Both files mock
// `@/lib/api/vfs` and, under the shared module graph, the LAST factory wins for
// ALL files, so both must be behaviorally identical: every override delegates to
// a `globalThis.__mock*` cell that each test (re)installs in beforeEach. We run
// the REAL delete/rename mutation hooks against these mocked client fns.
vi.mock('@/lib/api/vfs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/vfs')>();
  return {
    ...actual,

    signVfs: (...a: unknown[]) => ((globalThis as any).__mockSignVfs ?? (async () => ({ url: 'about:blank' })))(...a),

    deleteVfs: (...a: unknown[]) => ((globalThis as any).__mockDeleteVfs ?? (async () => ({ deleted: 1 })))(...a),

    renameVfs: (...a: unknown[]) => ((globalThis as any).__mockRenameVfs ?? (async () => ({ path: '' })))(...a),
  };
});

vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { VfsItemMenu } from '@/pages/canvas/explorer/VfsItemMenu';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({ lng: 'en', resources: {}, interpolation: { escapeValue: false } });

function renderRow(props: Partial<React.ComponentProps<typeof VfsItemMenu>>) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={testI18n}>
        <VfsItemMenu path="/data/foo.txt" name="foo.txt" isFolder={false} wfId="wf1" capabilities={['read', 'download', 'copy_path', 'rename', 'delete']} {...props}>
          <button type="button">row</button>
        </VfsItemMenu>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const openMenu = async () => {
  fireEvent.contextMenu(screen.getByText('row'));
  await screen.findByText('Copy Path');
};

beforeEach(() => {

  const g = globalThis as any;
  g.__mockSignVfs = vi.fn(async () => ({ url: 'blob:signed' }));
  g.__mockDeleteVfs = vi.fn(async () => ({ deleted: 1 }));
  g.__mockRenameVfs = vi.fn(async (a: { new_path: string }) => ({ path: a.new_path }));
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: vi.fn(async () => {}) },
  });
  // Stub the download anchor so click() doesn't hit jsdom navigation.
  vi.spyOn(document, 'createElement').mockImplementation(((tag: string) => {
    const el = Object.getPrototypeOf(document).constructor.prototype; // noop guard
    void el;
    const real = document.createElementNS('http://www.w3.org/1999/xhtml', tag) as HTMLElement;
    if (tag === 'a') real.click = vi.fn();
    return real;

  }) as any);
});

describe('VfsItemMenu', () => {
  it('durable file shows Download + Copy Path + Rename + Delete', async () => {
    renderRow({});
    await openMenu();
    expect(screen.getByText('Download')).toBeInTheDocument();
    expect(screen.getByText('Copy Path')).toBeInTheDocument();
    expect(screen.getByText('Rename')).toBeInTheDocument();
    expect(screen.getByText('Delete')).toBeInTheDocument();
  });

  it('run-tier file shows only its server-projected actions', async () => {
    renderRow({ wfId: undefined, runId: 'exec1', capabilities: ['read', 'download', 'copy_path'] });
    await openMenu();
    expect(screen.getByText('Download')).toBeInTheDocument();
    expect(screen.getByText('Copy Path')).toBeInTheDocument();
    expect(screen.queryByText('Rename')).not.toBeInTheDocument();
    expect(screen.queryByText('Delete')).not.toBeInTheDocument();
  });

  it('folder hides Download', async () => {
    renderRow({ isFolder: true, path: '/data/sub', name: 'sub' });
    await openMenu();
    expect(screen.queryByText('Download')).not.toBeInTheDocument();
    expect(screen.getByText('Rename')).toBeInTheDocument();
  });

  it('shows Upload file… only when uploadFolder is set', async () => {
    const { unmount } = renderRow({ isFolder: true, path: '/data', name: 'data', uploadFolder: 'data' });
    await openMenu();
    expect(screen.getByText('Upload file…')).toBeInTheDocument();
    unmount();

    renderRow({ isFolder: true, path: '/memory', name: 'memory', capabilities: ['read', 'copy_path'] });
    await openMenu();
    expect(screen.queryByText('Upload file…')).not.toBeInTheDocument();
  });

  it('Copy Path writes the path to the clipboard', async () => {
    renderRow({});
    await openMenu();
    fireEvent.click(screen.getByText('Copy Path'));
    await waitFor(() => expect(navigator.clipboard.writeText).toHaveBeenCalledWith('/data/foo.txt'));
  });

  it('Download signs by wf_id (durable) / run_id (run-tier)', async () => {

    const sign = (globalThis as any).__mockSignVfs;
    renderRow({});
    await openMenu();
    fireEvent.click(screen.getByText('Download'));
    await waitFor(() => expect(sign).toHaveBeenCalledWith({ path: '/data/foo.txt', wf_id: 'wf1', run_id: undefined }));
  });

  it('Delete confirms then calls the delete mutation with the path', async () => {

    const delMock = (globalThis as any).__mockDeleteVfs;
    renderRow({});
    await openMenu();
    fireEvent.click(screen.getByText('Delete'));
    // Confirm dialog → its destructive Delete button.
    const confirm = await screen.findByRole('button', { name: 'Delete' });
    fireEvent.click(confirm);
    await waitFor(() => expect(delMock).toHaveBeenCalledWith({ path: '/data/foo.txt', wf_id: 'wf1' }));
  });

  it('Rename joins parent + new name; rejects a name with "/"', async () => {

    const renameMock = (globalThis as any).__mockRenameVfs;
    renderRow({});
    await openMenu();
    fireEvent.click(screen.getByText('Rename'));
    const input = await screen.findByDisplayValue('foo.txt');
    // Reject a slashed name.
    fireEvent.change(input, { target: { value: 'a/b.txt' } });
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    expect(renameMock).not.toHaveBeenCalled();
    // Accept a plain rename → parent-joined new_path.
    fireEvent.change(input, { target: { value: 'bar.txt' } });
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    await waitFor(() =>
      expect(renameMock).toHaveBeenCalledWith({ wf_id: 'wf1', old_path: '/data/foo.txt', new_path: '/data/bar.txt' }),
    );
  });
});
