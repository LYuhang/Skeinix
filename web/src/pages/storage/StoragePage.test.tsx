import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import '@/lib/i18n';
import { StoragePage } from '@/pages/storage/StoragePage';

const refetch = vi.fn();
const mutateAsync = vi.fn();
const { downloadStorageBlobMock } = vi.hoisted(() => ({
  downloadStorageBlobMock: vi.fn(async () => new Blob(['image'], { type: 'image/png' })),
}));

vi.mock('@/lib/api/storage', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/api/storage')>(),
  downloadStorageBlob: downloadStorageBlobMock,
}));

vi.mock('@/lib/api/queries/storage', () => ({
  useStorageList: vi.fn(() => ({
    data: {
      path: '/mount',
      items: [
        { name: 'assets', path: '/mount/assets', kind: 'folder', size_bytes: null, modified_at: null, content_type: null, source: null, can_create_child: true, can_rename: true, can_delete: true, can_write: false },
        { name: 'notes.txt', path: '/mount/notes.txt', kind: 'file', size_bytes: 12, modified_at: '2026-07-18T00:00:00Z', content_type: 'text/plain', source: null, can_create_child: false, can_rename: true, can_delete: true, can_write: true },
        { name: 'preview.png', path: '/mount/preview.png', kind: 'file', size_bytes: 5, modified_at: '2026-07-18T00:00:00Z', content_type: 'image/png', source: null, can_create_child: false, can_rename: true, can_delete: true, can_write: false },
        { name: 'silence.wav', path: '/mount/silence.wav', kind: 'file', size_bytes: 44, modified_at: '2026-07-18T00:00:00Z', content_type: 'audio/wav', source: null, can_create_child: false, can_rename: true, can_delete: true, can_write: false },
      ],
      next_cursor: null,
      total_estimate: 4,
      readonly: false,
    },
    isLoading: false,
    isError: false,
    error: null,
    refetch,
  })),
  useStorageContent: vi.fn((path: string | null) => ({
    data: path ? {
      path,
      content_type: path.endsWith('.png') ? 'image/png' : path.endsWith('.wav') ? 'audio/wav' : 'text/plain',
      content: path.endsWith('.png') || path.endsWith('.wav') ? null : 'hello storage',
      size_bytes: 12,
      truncated: false,
    } : undefined,
    isLoading: false,
    isError: false,
    error: null,
    refetch,
  })),
  useUploadStorageFile: vi.fn(() => ({ mutateAsync, isPending: false })),
  useMkdirStorage: vi.fn(() => ({ mutateAsync, isPending: false })),
  useRenameStorage: vi.fn(() => ({ mutateAsync, isPending: false })),
  useDeleteStorage: vi.fn(() => ({ mutateAsync, isPending: false })),
  useWriteStorageContent: vi.fn(() => ({ mutateAsync, isPending: false })),
}));

vi.mock('@/lib/timezone', () => ({
  useFormatDateTime: () => (value?: string | null) => value ?? '—',
}));

function LocationProbe() {
  const location = useLocation();
  return <output data-testid="location">{location.search}</output>;
}

function renderStorage(entry = '/storage?path=%2Fmount&q=note&sort=size') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/storage" element={<><StoragePage /><LocationProbe /></>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('<StoragePage>', () => {
  beforeEach(() => {
    refetch.mockReset();
    mutateAsync.mockReset();
    downloadStorageBlobMock.mockClear();
    sessionStorage.clear();
    localStorage.clear();
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:storage-preview') });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
  });

  it('restores location state and only opens a file on an explicit open gesture', async () => {
    const user = userEvent.setup();
    renderStorage();

    expect(screen.getByPlaceholderText('Search current folder')).toHaveValue('note');
    expect(screen.getByTestId('location')).toHaveTextContent('path=%2Fmount');

    const fileButton = screen.getByRole('button', { name: 'notes.txt' });
    await user.click(fileButton);
    expect(screen.queryByRole('heading', { name: '/mount/notes.txt' })).not.toBeInTheDocument();

    fireEvent.doubleClick(fileButton.closest('tr') as HTMLTableRowElement);
    expect(await screen.findByRole('heading', { name: '/mount/notes.txt' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('location')).toHaveTextContent('file=%2Fmount%2Fnotes.txt'));
  });

  it('supports Arrow navigation and Enter to open the focused row', async () => {
    const user = userEvent.setup();
    renderStorage('/storage?path=%2Fmount');

    const folderButton = screen.getByRole('button', { name: 'assets' });
    folderButton.focus();
    await user.keyboard('{ArrowDown}{Enter}');

    expect(await screen.findByRole('heading', { name: '/mount/notes.txt' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'notes.txt' })).toHaveFocus();
  });

  it('loads media through the authenticated blob client before rendering it', async () => {
    const user = userEvent.setup();
    renderStorage('/storage?path=%2Fmount');

    const imageButton = screen.getByRole('button', { name: 'preview.png' });
    await user.dblClick(imageButton);

    await waitFor(() => expect(downloadStorageBlobMock).toHaveBeenCalledWith('/mount/preview.png'));
    expect(await screen.findByRole('img', { name: 'preview.png' })).toHaveAttribute('src', 'blob:storage-preview');

    const audioButton = screen.getByRole('button', { name: 'silence.wav' });
    await user.dblClick(audioButton);

    await waitFor(() => expect(downloadStorageBlobMock).toHaveBeenCalledWith('/mount/silence.wav'));
    expect(await screen.findByLabelText('silence.wav')).toHaveAttribute('src', 'blob:storage-preview');
  });

  it('does not allow an unchanged filename to be submitted as a rename', async () => {
    const user = userEvent.setup();
    renderStorage('/storage?path=%2Fmount');

    fireEvent.contextMenu(screen.getByRole('button', { name: 'notes.txt' }).closest('tr') as HTMLTableRowElement);
    await user.click(screen.getByRole('menuitem', { name: 'Rename' }));

    expect(screen.getByRole('button', { name: 'Rename' })).toBeDisabled();
    expect(mutateAsync).not.toHaveBeenCalled();
  });
});
