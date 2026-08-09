import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAuthStore } from '@/stores/auth';

describe('auth extension exchange hydration', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      token: null,
      authenticated: false,
      user: null,
      bootstrapped: false,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('redeems a one-time code without persisting a raw Session', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            user_id: 'user_embed',
            tenant_id: 'tenant_embed',
            email: 'embed@example.test',
            display_name: 'Embed User',
          }),
          {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          },
        ),
      );
    vi.stubGlobal('fetch', fetchMock);

    await useAuthStore.getState().bootstrap('one_time_exchange_code');

    const exchangeRequest = fetchMock.mock.calls[0]?.[0] as Request;
    const meRequest = fetchMock.mock.calls[1]?.[0] as Request;
    expect(exchangeRequest.url).toContain('/api/v1/auth/extension/exchange');
    expect(exchangeRequest.credentials).toBe('include');
    await expect(exchangeRequest.clone().json()).resolves.toEqual({
      code: 'one_time_exchange_code',
    });
    expect(meRequest.url).toContain('/api/v1/auth/me');
    expect(meRequest.headers.has('Authorization')).toBe(false);
    expect(localStorage.getItem('vibecanvas.token')).toBeNull();
    expect(useAuthStore.getState()).toMatchObject({
      token: null,
      authenticated: true,
      bootstrapped: true,
      user: {
        user_id: 'user_embed',
        tenant_id: 'tenant_embed',
        email: 'embed@example.test',
        displayName: 'Embed User',
      },
    });
  });
});
