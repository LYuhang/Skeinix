import { expect, test } from '@playwright/test';

const APP_URL = process.env.VIBECANVAS_WEBAUTHN_E2E_URL
  ?? 'http://localhost:5173/';

function appUrl(path: string): string {
  return new URL(path.replace(/^\//, ''), APP_URL).toString();
}

test.use({
  ignoreHTTPSErrors: true,
  launchOptions: {
    args: [
      // Keep the WebAuthn origin on localhost (which Chromium treats as a
      // trustworthy development origin) while forcing IPv4 for native runs.
      '--host-resolver-rules=MAP localhost 127.0.0.1',
      '--no-proxy-server',
    ],
  },
});

test('real WebAuthn enrollment keeps passkey step-up available', async ({
  context,
  page,
}) => {
  test.setTimeout(90_000);
  await page.addInitScript(() => {
    window.localStorage.setItem('vibecanvas.locale', 'en');
  });
  const cdp = await context.newCDPSession(page);
  await cdp.send('WebAuthn.enable');
  const added = await cdp.send('WebAuthn.addVirtualAuthenticator', {
    options: {
      protocol: 'ctap2',
      transport: 'internal',
      hasResidentKey: true,
      hasUserVerification: true,
      isUserVerified: true,
      automaticPresenceSimulation: true,
    },
  }) as { authenticatorId: string };

  const suffix = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  const email = `webauthn-e2e-${suffix}@example.com`;
  const password = 'WebAuthn-E2E-password-42!';

  try {
    await page.goto(appUrl('/signup'));
    await page.locator('#signup-username').fill(`webauthn_${suffix}`);
    await page.locator('#signup-email').fill(email);
    await page.locator('#signup-password').fill(password);
    await page.locator('#signup-confirm').fill(password);
    await page.getByRole('button', { name: /create account/i }).click();
    await expect(page).toHaveURL(/\/chat$/, { timeout: 20_000 });

    await page.goto(appUrl('/settings?tab=account'));
    await expect(page.getByTestId('passkey-security-section')).toBeVisible();
    await expect(page.getByText('Multi-factor authentication')).toHaveCount(0);
    await expect(page.getByText('Authenticator app')).toHaveCount(0);
    await page.getByRole('button', { name: 'Add passkey', exact: true }).click();
    await page.locator('#passkey-name').fill('Chromium virtual passkey');
    await page.locator('#passkey-password').fill(password);
    await page.getByRole('dialog').getByRole('button', {
      name: 'Add passkey',
      exact: true,
    }).click();
    await expect(page.getByText('Chromium virtual passkey')).toBeVisible({
      timeout: 20_000,
    });

    const credentials = await cdp.send('WebAuthn.getCredentials', {
      authenticatorId: added.authenticatorId,
    }) as { credentials: Array<{ signCount: number }> };
    expect(credentials.credentials).toHaveLength(1);
    expect(credentials.credentials[0]?.signCount).toBeGreaterThanOrEqual(0);
  } finally {
    await cdp.send('WebAuthn.removeVirtualAuthenticator', {
      authenticatorId: added.authenticatorId,
    }).catch(() => undefined);
    await cdp.send('WebAuthn.disable').catch(() => undefined);
  }
});
