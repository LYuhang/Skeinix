import { beforeEach, describe, expect, it, vi } from 'vitest';

const { sessionFetch } = vi.hoisted(() => ({ sessionFetch: vi.fn() }));

vi.mock('@/lib/base-path', () => ({ getApiBase: () => '/prefix' }));
vi.mock('@/lib/api/session-fetch', () => ({ sessionFetch }));

import { mintBrowserToken } from '@/lib/api/browser';

describe('browser WebSocket capability mint', () => {
  beforeEach(() => {
    sessionFetch.mockReset();
  });

  it('binds the request to the stable extension browser id', async () => {
    sessionFetch.mockResolvedValue(new Response(
      JSON.stringify({ token: 'scoped-token' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ));

    await expect(mintBrowserToken('wf-1', 'browser-1')).resolves.toBe('scoped-token');
    expect(sessionFetch).toHaveBeenCalledWith(
      '/prefix/api/v1/browser/token',
      expect.objectContaining({
        method: 'POST',
        credentials: 'include',
        body: JSON.stringify({ wf_id: 'wf-1', browser_id: 'browser-1' }),
      }),
    );
  });
});
