/**
 * Deployments T14 — `DeploymentsListPage` smoke test.
 *
 * Mocks `@/lib/api/deployments` (the hand-rolled-types module) so the
 * test can assert the page renders one row per deployment returned by
 * the API. Same module-mock pattern as `TasksListPage.test.tsx` —
 * MSW would be heavier here, and the page contract is a single typed
 * fetch wrapper.
 *
 * We do NOT exercise the create modal here — it has its own test file
 * (`CreateDeploymentModal.test.tsx`) covering the one-shot-secret flow.
 */
import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';
import { TooltipProvider } from '@/components/ui/tooltip';

vi.mock('@/lib/api/deployments', () => ({
  listDeployments: vi.fn(async () => ({
    items: [
      {
        id: '00000000-0000-0000-0000-000000000001',
        tenant_id: '00000000-0000-0000-0000-0000000000aa',
        user_id: '00000000-0000-0000-0000-0000000000bb',
        wf_id: 'wf_42',
        name: 'API bot',
        slug: 'bot',
        trigger_type: 'api',
        version_pin: 'head',
        pinned_major: null,
        pinned_sub: null,
        enabled: true,
        rate_limit_qps: 10,
        invoke_count: 42,
        last_invoked_at: null,
        created_at: '2026-05-24T00:00:00Z',
        updated_at: null,
        deleted_at: null,
      },
      {
        id: '00000000-0000-0000-0000-000000000002',
        tenant_id: '00000000-0000-0000-0000-0000000000aa',
        user_id: '00000000-0000-0000-0000-0000000000bb',
        wf_id: 'wf_43',
        name: 'Webhook receiver',
        slug: 'webhook-receiver',
        trigger_type: 'webhook',
        version_pin: 'head',
        pinned_major: null,
        pinned_sub: null,
        enabled: true,
        rate_limit_qps: 10,
        invoke_count: 7,
        last_invoked_at: '2026-08-22T01:00:00Z',
        created_at: '2026-08-21T00:00:00Z',
        updated_at: null,
        deleted_at: null,
      },
    ],
    limit: 50,
    offset: 0,
  })),
  patchDeployment: vi.fn(),
  deleteDeployment: vi.fn(),
}));

import { DeploymentsListPage } from '@/pages/deployments/DeploymentsListPage';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderWithProviders(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={testI18n}>
        <TooltipProvider>
          <MemoryRouter>{ui}</MemoryRouter>
        </TooltipProvider>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe('<DeploymentsListPage>', () => {
  it('renders the page header without throwing', () => {
    renderWithProviders(<DeploymentsListPage />);
    expect(screen.getByText('Deployment')).toBeInTheDocument();
  });

  it('renders a row for each deployment returned by the API', async () => {
    renderWithProviders(<DeploymentsListPage />);
    await waitFor(() =>
      expect(screen.getByText('API bot')).toBeInTheDocument(),
    );
    // Endpoint appears in the secondary line of the name column.
    expect(screen.getByText('/api/v1/deployments/bot/invoke')).toBeInTheDocument();
    // Invoke counter is also reflected in the summary card.
    expect(screen.getAllByText('42').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', {
      name: 'Open actions menu for API bot',
    })).toBeInTheDocument();
    expect(screen.getByText('Webhook receiver')).toBeInTheDocument();
    expect(screen.getByText('/api/v1/deployments/webhook-receiver/webhook')).toBeInTheDocument();
    expect(screen.getByText('Webhook')).toBeInTheDocument();
  });
});
