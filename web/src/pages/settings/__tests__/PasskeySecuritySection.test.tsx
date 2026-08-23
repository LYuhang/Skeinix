import { render, screen } from '@testing-library/react';
import i18n from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import en from '@/lib/i18n/locales/en.json';
import zh from '@/lib/i18n/locales/zh.json';

vi.mock('@/lib/api/passkeys', () => ({
  getWebAuthnStatus: vi.fn(async () => ({ enabled: false, credentials: [] })),
  beginWebAuthnRegistration: vi.fn(),
  deleteWebAuthnCredential: vi.fn(),
  finishWebAuthnRegistration: vi.fn(),
}));

vi.mock('@/lib/timezone', () => ({ useFormatDateTime: () => (value: string) => value }));

import { PasskeySecuritySection } from '@/pages/settings/PasskeySecuritySection';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'zh',
  fallbackLng: 'en',
  resources: { en: { translation: en }, zh: { translation: zh } },
  interpolation: { escapeValue: false },
});

describe('<PasskeySecuritySection>', () => {
  beforeEach(async () => {
    await testI18n.changeLanguage('zh');
  });

  it('uses native Chinese copy and exposes no authenticator MFA actions', async () => {
    render(
      <I18nextProvider i18n={testI18n}>
        <PasskeySecuritySection />
      </I18nextProvider>,
    );

    expect(await screen.findByText('通行密钥与安全密钥')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '添加通行密钥' })).toBeInTheDocument();
    expect(screen.queryByText('多重身份验证')).not.toBeInTheDocument();
    expect(screen.queryByText('身份验证器应用')).not.toBeInTheDocument();
    expect(screen.queryByText('Multi-factor authentication')).not.toBeInTheDocument();
    expect(screen.queryByText('Authenticator app')).not.toBeInTheDocument();
  });

  it('uses native English copy and exposes no recovery-code actions', async () => {
    await testI18n.changeLanguage('en');
    render(
      <I18nextProvider i18n={testI18n}>
        <PasskeySecuritySection />
      </I18nextProvider>,
    );

    expect(await screen.findByText('Passkeys and security keys')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add passkey' })).toBeInTheDocument();
    expect(screen.queryByText(/recovery code/i)).not.toBeInTheDocument();
  });
});
