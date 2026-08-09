import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import i18n from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { describe, expect, it, vi } from 'vitest';

import { PlatformManagementPage } from '@/pages/management/PlatformManagementPage';

vi.mock('@/lib/api/platform-management', () => ({
  getPlatformManagementOverview: vi.fn(async () => ({
    role: 'platform_support',
    generated_at: '2026-08-02T00:00:00Z',
    identity: {
      registered_users: 12,
      active_users: 11,
      online_users_5m: 3,
      registered_users_24h: 2,
      personal_workspaces: 9,
      company_workspaces: 3,
    },
    organizations: [],
    host: {
      cpu_count: 8,
      load_average_1m: 1,
      load_average_5m: 0.8,
      load_average_15m: 0.7,
      memory: { total_bytes: 1024, available_bytes: 512 },
      disk: { total_bytes: 4096, free_bytes: 2048 },
      scope: 'current_api_host',
    },
    sandboxes: { resident: 1, capacity: 4, busy: 1, resident_leases: 1, pending_closes: 0 },
    privacy: { content_visible: false, user_profiles_visible: false, scope: 'aggregate_and_lifecycle_metadata_only' },
  })),
  getPlatformAuditReport: vi.fn(async () => ({
    role: 'platform_support',
    generated_at: '2026-08-02T00:00:00Z',
    window_hours: 168,
    bucket: 'day',
    categories: [
      { category: 'identity', total: 4, failures: 1, series: [{ ts: '2026-08-01T00:00:00Z', total: 4, failures: 1 }], actions: [] },
      { category: 'access_security', total: 2, failures: 0, series: [], actions: [] },
      { category: 'resources', total: 1, failures: 0, series: [{ ts: '2026-08-01T00:00:00Z', total: 1, failures: 0 }], actions: [{ action: 'workflow.delete', total: 1, failures: 0 }] },
      { category: 'data_lifecycle', total: 0, failures: 0, series: [], actions: [] },
      { category: 'runtime_operations', total: 0, failures: 0, series: [], actions: [] },
    ],
    recent_events: [{ event_id: 'event-1', category: 'resources', action: 'workflow.delete', target_type: 'workflow', outcome: 'success', created_at: '2026-08-01T00:00:00Z' }],
    catalog: [
      { category: 'identity', actions: [], missing_objects: [], coverage: 'complete' },
      { category: 'access_security', actions: [], missing_objects: [], coverage: 'complete' },
      { category: 'resources', actions: ['workflow.delete'], missing_objects: ['task'], coverage: 'partial' },
      { category: 'data_lifecycle', actions: [], missing_objects: ['vfs_path'], coverage: 'partial' },
      { category: 'runtime_operations', actions: [], missing_objects: ['agent_run'], coverage: 'partial' },
    ],
    privacy: { content_visible: false, identities_visible: false, customer_resource_identifiers_visible: false, private_payload_decrypted: false },
  })),
}));

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

describe('platform management audit', () => {
  it('opens a category card into its trend and redacted event detail', async () => {
    const user = userEvent.setup();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <I18nextProvider i18n={testI18n}>
          <PlatformManagementPage />
        </I18nextProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText('Registered users')).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: /audit/i }));
    await user.click(await screen.findByRole('button', { name: /resources/i }));
    expect(screen.getByText('Event trend')).toBeInTheDocument();
    expect(screen.getAllByText('workflow.delete')).toHaveLength(2);
    expect(screen.getByText('Metadata-only operator view')).toBeInTheDocument();
    expect(screen.queryByText(/example\.com/i)).toBeNull();
  });
});
