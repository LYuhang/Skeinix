import { describe, expect, it, vi, afterEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { createElement } from 'react';

vi.mock('@/lib/api/vfs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/vfs')>();
  return {
    ...actual,
    uploadVfsFile: vi.fn(async () => ({
      path: '/mount/x.csv',
      size_bytes: 1,
      content_type: 'text/csv',
      replaced: false,
    })),
  };
});

import { uploadVfsFile } from '@/lib/api/vfs';
import { useUploadVfsFile } from '@/lib/api/queries/vfs';

afterEach(() => vi.clearAllMocks());

describe('useUploadVfsFile', () => {
  it('calls uploadVfsFile and invalidates the useVfsList query key on success', async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(qc, 'invalidateQueries');
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: qc }, children);

    const { result } = renderHook(() => useUploadVfsFile('mount-scope', 'mount'), { wrapper });

    const file = new File(['x'], 'x.csv', { type: 'text/csv' });
    result.current.mutate(file);

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(uploadVfsFile).toHaveBeenCalledWith('mount-scope', file, 'mount');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['vfs', 'list', 'mount-scope'] });
  });
});
