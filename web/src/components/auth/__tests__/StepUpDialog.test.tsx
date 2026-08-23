import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const api = vi.hoisted(() => ({
  getWebAuthnStatus: vi.fn(),
  beginWebAuthnAuthentication: vi.fn(),
  finishWebAuthnAuthentication: vi.fn(),
  beginWebAuthnRegistration: vi.fn(),
  finishWebAuthnRegistration: vi.fn(),
}));
const browser = vi.hoisted(() => ({
  getWebAuthnCredential: vi.fn(),
  createWebAuthnCredential: vi.fn(),
}));

vi.mock('@/lib/api/passkeys', () => api);
vi.mock('@/lib/auth/webauthn-browser', () => browser);

import { StepUpDialog } from '@/components/auth/StepUpDialog';
import { requestWebAuthnStepUp } from '@/lib/auth/step-up-broker';

describe('<StepUpDialog>', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.beginWebAuthnAuthentication.mockResolvedValue({ challenge: 'challenge' });
    api.finishWebAuthnAuthentication.mockResolvedValue({ authentication_strength: 'webauthn' });
    api.beginWebAuthnRegistration.mockResolvedValue({ challenge: 'challenge' });
    api.finishWebAuthnRegistration.mockResolvedValue({ authentication_strength: 'webauthn' });
    browser.getWebAuthnCredential.mockResolvedValue({ id: 'credential' });
    browser.createWebAuthnCredential.mockResolvedValue({ id: 'credential' });
  });

  it('claims a high-risk request and completes it after passkey verification', async () => {
    api.getWebAuthnStatus.mockResolvedValue({ enabled: true, credentials: [] });
    render(<StepUpDialog />);

    const completed = requestWebAuthnStepUp();
    const button = await screen.findByRole('button', { name: /use passkey/i });
    fireEvent.click(button);

    await expect(completed).resolves.toBe(true);
    expect(api.beginWebAuthnAuthentication).toHaveBeenCalledOnce();
    expect(browser.getWebAuthnCredential).toHaveBeenCalledOnce();
    expect(api.finishWebAuthnAuthentication).toHaveBeenCalledWith({ id: 'credential' });
    await waitFor(() => {
      expect(screen.queryByTestId('webauthn-step-up-dialog')).toBeNull();
    });
  });

  it('offers inline enrollment when no passkey exists', async () => {
    api.getWebAuthnStatus.mockResolvedValue({ enabled: false, credentials: [] });
    render(<StepUpDialog />);

    const completed = requestWebAuthnStepUp();
    expect(await screen.findByText(/no passkey is registered yet/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/current password/i), {
      target: { value: 'correct horse battery staple' },
    });
    fireEvent.change(screen.getByLabelText(/passkey name/i), {
      target: { value: 'Work laptop' },
    });
    fireEvent.click(screen.getByRole('button', { name: /add passkey and continue/i }));

    await expect(completed).resolves.toBe(true);
    expect(api.beginWebAuthnRegistration).toHaveBeenCalledWith('correct horse battery staple');
    expect(api.finishWebAuthnRegistration).toHaveBeenCalledWith(
      { id: 'credential' },
      'Work laptop',
    );
  });

  it('releases the blocked request without retry when cancelled', async () => {
    api.getWebAuthnStatus.mockResolvedValue({ enabled: true, credentials: [] });
    render(<StepUpDialog />);

    const completed = requestWebAuthnStepUp();
    fireEvent.click(await screen.findByRole('button', { name: /cancel/i }));

    await expect(completed).resolves.toBe(false);
    expect(api.beginWebAuthnAuthentication).not.toHaveBeenCalled();
  });
});
