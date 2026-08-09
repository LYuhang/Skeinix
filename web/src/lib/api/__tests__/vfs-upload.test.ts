import { afterEach, describe, expect, it, vi } from 'vitest';
import { listVfsRun, uploadVfsFile } from '@/lib/api/vfs';

// stub the auth store so authedFetch can read a token
vi.mock('@/stores/auth', () => ({
  useAuthStore: { getState: () => ({ token: 't0k', handle401: () => {} }) },
}));

afterEach(() => vi.restoreAllMocks());

function mockFetchOnce(body: unknown) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
}

describe('uploadVfsFile', () => {
  it('POSTs FormData to /api/v1/vfs/upload with an explicit folder', async () => {
    const spy = mockFetchOnce({
      path: '/mount/sales.csv',
      size_bytes: 12,
      content_type: 'text/csv',
      replaced: false,
    });
    const file = new File(['a,b,c'], 'sales.csv', { type: 'text/csv' });
    const out = await uploadVfsFile('mount-scope', file, 'mount');

    expect(spy).toHaveBeenCalledTimes(1);
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain('/api/v1/vfs/upload?wf_id=mount-scope&folder=mount');
    expect((init as RequestInit).method).toBe('POST');
    const body = (init as RequestInit).body;
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get('file')).toBe(file);

    expect(out).toEqual({
      path: '/mount/sales.csv',
      size_bytes: 12,
      content_type: 'text/csv',
      replaced: false,
    });
  });

  it('throws on a non-ok response', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'too big' }), { status: 413 }),
    );
    const file = new File(['x'], 'big.bin');
    await expect(uploadVfsFile('mount-scope', file, 'mount')).rejects.toThrow(/uploadVfsFile failed: 413/);
  });
});

describe('listVfsRun', () => {
  it('treats a not-yet-created workflow run tier as an empty directory', async () => {
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'vfs_run_not_found' }), { status: 404 }),
    );

    await expect(listVfsRun('wf-before-first-run')).resolves.toEqual({ entries: [] });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(String(spy.mock.calls[0][0])).toContain('/api/v1/vfs/runs/wf-before-first-run');
  });

  it('keeps non-404 failures visible', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: 'upstream unavailable' }), { status: 503 }),
    );

    await expect(listVfsRun('wf1')).rejects.toThrow(/listVfsRun failed: 503/);
  });
});
