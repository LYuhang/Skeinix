import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const browser = vi.hoisted(() => ({
  getWebAuthnCredential: vi.fn(),
}));

vi.mock('@/lib/auth/webauthn-browser', () => browser);

import { LoginMfaForm } from '@/components/auth/LoginMfaForm';
import { useAuthStore } from '@/stores/auth';

const pending = {
  loginChallenge: 'opaque-login-challenge-token-value',
  methods: ['webauthn', 'totp', 'recovery'] as const,
  webauthnOptions: { challenge: 'public-key-challenge' },
  expiresAt: '2026-08-01T20:00:00Z',
};

describe('<LoginMfaForm>', () => {
  const completeCode = vi.fn();
  const completeWebAuthn = vi.fn();
  const refreshOptions = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    completeCode.mockResolvedValue(undefined);
    completeWebAuthn.mockResolvedValue(undefined);
    refreshOptions.mockResolvedValue({ challenge: 'refreshed' });
    browser.getWebAuthnCredential.mockResolvedValue({ id: 'credential' });
    useAuthStore.setState({
      completeLoginMfaCode: completeCode,
      completeLoginMfaWebAuthn: completeWebAuthn,
      refreshLoginWebAuthnOptions: refreshOptions,
    });
  });

  it('offers both passkey and authenticator/recovery paths', () => {
    render(
      <LoginMfaForm
        pending={{ ...pending, methods: [...pending.methods] }}
        onAuthenticated={vi.fn()}
        onBack={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: /use passkey/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/authenticator or recovery code/i)).toBeInTheDocument();
  });

  it('completes passkey login using options returned by password verification', async () => {
    const onAuthenticated = vi.fn();
    render(
      <LoginMfaForm
        pending={{ ...pending, methods: [...pending.methods] }}
        onAuthenticated={onAuthenticated}
        onBack={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /use passkey/i }));
    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce());
    expect(refreshOptions).not.toHaveBeenCalled();
    expect(browser.getWebAuthnCredential).toHaveBeenCalledWith(
      pending.webauthnOptions,
    );
    expect(completeWebAuthn).toHaveBeenCalledWith(
      pending.loginChallenge,
      { id: 'credential' },
    );
  });

  it('accepts a recovery code through the one-time-code path', async () => {
    const onAuthenticated = vi.fn();
    render(
      <LoginMfaForm
        pending={{ ...pending, methods: [...pending.methods] }}
        onAuthenticated={onAuthenticated}
        onBack={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText(/authenticator or recovery code/i), {
      target: { value: 'ABCDE-FGHIJ-KLMNO-PQRST-UVWXY' },
    });
    fireEvent.click(screen.getByRole('button', { name: /verify and sign in/i }));

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledOnce());
    expect(completeCode).toHaveBeenCalledWith(
      pending.loginChallenge,
      'ABCDE-FGHIJ-KLMNO-PQRST-UVWXY',
    );
  });
});
