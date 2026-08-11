/**
 * Deployments T14 — `DeploymentDetailPage` smoke test.
 *
 * Mocks the deployments API so the page can render against a stable
 * fixture. Asserts:
 *   * The six tab triggers appear.
 *   * The Overview tab — the default selected tab — surfaces the deployment
 *     endpoint and status.
 *   * The "Rotate API key" button appears for trigger_type=api
 *     deployments (it's hidden for webhook / cron, but T14 covers the
 *     api path; the conditional render is exercised here).
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';
import { TooltipProvider } from '@/components/ui/tooltip';

const DEP_ID = '00000000-0000-0000-0000-000000000abc';

vi.mock('@/lib/api/deployments', () => ({
  getDeployment: vi.fn(async () => ({
    id: DEP_ID,
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
    invoke_count: 5,
    last_invoked_at: null,
    last_fire_at: null,
    cron_expr: null,
    cron_tz: null,
    access: {
      capabilities: [
        'view',
        'update',
        'inspect_runs',
        'execute',
        'manage_secret',
        'manage_access',
      ],
      effective_role: 'manager',
      source: 'computed',
    },
    created_at: '2026-05-24T00:00:00Z',
    updated_at: null,
    deleted_at: null,
  })),
  getMetrics: vi.fn(async () => ({
    series: [],
    bucket: 'hour',
    from: '2026-05-23T00:00:00Z',
    to: '2026-05-24T00:00:00Z',
  })),
  getHistory: vi.fn(async () => ({
    items: [],
    next_cursor: null,
    limit: 50,
  })),
  patchDeployment: vi.fn(),
  rotateKey: vi.fn(),
  testInvoke: vi.fn(),
}));

vi.mock('@/lib/api/queries/workflow', () => ({
  useWorkflow: () => ({
    data: {
      workflow: {
        node_1: {
          node_type: 'StartNode',
          input_fields: {
            analysis_focus: { type: 'string' },
          },
        },
      },
    },
  }),
}));

import { DeploymentDetailPage } from '@/pages/deployments/DeploymentDetailPage';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderAt(depId: string) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={testI18n}>
        <TooltipProvider>
          <MemoryRouter initialEntries={[`/deployments/${depId}`]}>
            <Routes>
              <Route
                path="/deployments/:depId"
                element={<DeploymentDetailPage />}
              />
            </Routes>
          </MemoryRouter>
        </TooltipProvider>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe('<DeploymentDetailPage>', () => {
  it('renders deployment header, all six tabs, and the Overview tab content', async () => {
    const user = userEvent.setup();
    renderAt(DEP_ID);

    // Header — deployment name surfaces once the query resolves.
    await waitFor(() => {
      expect(screen.getByText('API bot')).toBeInTheDocument();
    });
    // Endpoint in the sub-header.
    expect(screen.getAllByText('/api/v1/deployments/bot/invoke').length).toBeGreaterThan(0);

    // All six tab triggers are rendered (Radix Tabs renders each
    // <TabsTrigger> as a real button regardless of which is active).
    expect(
      screen.getByRole('tab', { name: /^Overview$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /^Config$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /^Runs \/ Logs$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /^Monitoring$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /^Test$/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: /^Security$/i }),
    ).toBeInTheDocument();

    // Overview tab is the default — status and copy affordance are visible.
    expect(screen.getAllByText('Active').length).toBeGreaterThan(0);
    expect(
      screen.getAllByRole('button', { name: /copy endpoint/i }).length,
    ).toBeGreaterThan(0);

    // Security tab owns API key rotation.
    await user.click(screen.getByRole('tab', { name: /^Security$/i }));
    expect(
      await screen.findByRole('button', { name: /rotate api key/i }),
    ).toBeInTheDocument();
  });

  it('uses the workflow StartNode fields in code examples and test inputs', async () => {
    const user = userEvent.setup();
    renderAt(DEP_ID);

    await screen.findByText('API bot');
    await user.click(screen.getByRole('tab', { name: /^Code examples$/i }));
    expect(screen.getByTestId('deployment-code-curl')).toHaveTextContent(
      '"analysis_focus":"<analysis_focus>"',
    );

    await user.click(screen.getByRole('tab', { name: /^Test$/i }));
    expect(screen.getByRole('textbox', { name: 'Inputs (JSON)' })).toHaveValue(
      '{\n  "analysis_focus": "<analysis_focus>"\n}',
    );
  });
});
