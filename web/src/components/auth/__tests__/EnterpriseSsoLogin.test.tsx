import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import '@/lib/i18n';
import { server } from '@/__tests__/msw-handlers';
import { EnterpriseSsoLogin } from '@/components/auth/EnterpriseSsoLogin';

describe('<EnterpriseSsoLogin>', () => {
  it('discovers providers by normalized organization slug and exposes the OIDC start URL', async () => {
    let requestedSlug = '';
    server.use(http.get(
      '*/api/v1/auth/sso/organizations/:slug/providers',
      ({ params }) => {
        requestedSlug = String(params.slug);
        return HttpResponse.json({
          items: [{
            provider_id: '92e070ec-0a92-4ff6-9f54-bfe4b9cde755',
            display_name: 'Corporate Identity',
          }],
        });
      },
    ));
    const user = userEvent.setup();
    render(<EnterpriseSsoLogin />);

    await user.type(screen.getByLabelText(/organization slug/i), ' Acme-Team ');
    await user.click(screen.getByRole('button', { name: /^continue$/i }));

    const provider = await screen.findByRole('link', {
      name: /continue with corporate identity/i,
    });
    expect(requestedSlug).toBe('acme-team');
    expect(provider).toHaveAttribute(
      'href',
      'http://localhost/api/v1/auth/sso/providers/'
        + '92e070ec-0a92-4ff6-9f54-bfe4b9cde755/start?return_to=%2Fchat',
    );
  });

  it('uses the same non-enumerating message for an empty discovery result', async () => {
    server.use(http.get(
      '*/api/v1/auth/sso/organizations/:slug/providers',
      () => HttpResponse.json({ items: [] }),
    ));
    const user = userEvent.setup();
    render(<EnterpriseSsoLogin />);

    await user.type(screen.getByLabelText(/organization slug/i), 'unknown-org');
    await user.click(screen.getByRole('button', { name: /^continue$/i }));

    expect(await screen.findByText(/no company sign-in option was found/i))
      .toBeInTheDocument();
  });
});
