import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useAuthStore } from '@/stores/auth';

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('login MFA auth-store flow', () => {
  beforeEach(() => {
    localStorage.clear();
    useAuthStore.setState({
      token: null,
      authenticated: false,
      user: null,
      bootstrapped: false,
      organizationSwitching: false,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('keeps the browser unauthenticated while a second factor is pending', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        mfa_required: true,
        login_challenge: 'opaque-login-challenge-token-value',
        methods: ['webauthn', 'totp', 'recovery'],
        webauthn_options: { challenge: 'public-key-challenge' },
        expires_at: '2026-08-01T20:00:00Z',
      }, 202),
    );
    vi.stubGlobal('fetch', fetchMock);

    const pending = await useAuthStore.getState().login(
      'mfa@example.test',
      'correct horse battery staple',
    );

    expect(pending).toEqual({
      loginChallenge: 'opaque-login-challenge-token-value',
      methods: ['webauthn', 'totp', 'recovery'],
      webauthnOptions: { challenge: 'public-key-challenge' },
      expiresAt: '2026-08-01T20:00:00Z',
    });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(useAuthStore.getState()).toMatchObject({
      authenticated: false,
      user: null,
    });
  });

  it('hydrates the account only after a valid login factor creates a Session', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({
        user: {
          user_id: 'user_mfa',
          email: 'mfa@example.test',
          display_name: 'MFA User',
        },
      }))
      .mockResolvedValueOnce(jsonResponse({
        user_id: 'user_mfa',
        tenant_id: 'tenant_mfa',
        email: 'mfa@example.test',
        display_name: 'MFA User',
      }));
    vi.stubGlobal('fetch', fetchMock);

    await useAuthStore.getState().completeLoginMfaCode(
      'opaque-login-challenge-token-value',
      '123456',
    );

    const factorRequest = fetchMock.mock.calls[0]?.[0] as Request;
    expect(factorRequest.url).toContain('/api/v1/auth/login/mfa/totp');
    await expect(factorRequest.clone().json()).resolves.toEqual({
      login_challenge: 'opaque-login-challenge-token-value',
      code: '123456',
    });
    expect(useAuthStore.getState()).toMatchObject({
      authenticated: true,
      user: {
        user_id: 'user_mfa',
        tenant_id: 'tenant_mfa',
        email: 'mfa@example.test',
        displayName: 'MFA User',
      },
    });
  });
});
