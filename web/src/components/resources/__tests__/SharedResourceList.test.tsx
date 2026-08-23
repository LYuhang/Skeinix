import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import i18n from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SharedResourceList } from '@/components/resources/SharedResourceList';
import { listSharedResources } from '@/lib/api/organizations';

vi.mock('@/lib/api/organizations', () => ({
  listSharedResources: vi.fn(),
}));

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderList() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={testI18n}>
        <MemoryRouter>
          <SharedResourceList resourceType="workflow" />
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe('SharedResourceList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(listSharedResources).mockResolvedValue({
      items: [{
        resource_type: 'workflow',
        resource_id: 'workflow-1',
        name: 'Release workflow',
        description: 'Coordinates the release process.',
        updated_at: '2026-08-23T01:00:00Z',
        access: {
          capabilities: ['view_metadata', 'view', 'export'],
          effective_role: 'viewer',
          source: 'shared',
        },
        provenance: {
          ownership_scope: 'personal',
          origin_type: 'created',
          owner: { type: 'user', display_name: 'Alice' },
          created_by: { type: 'user', display_name: 'Alice' },
        },
      }],
      next_offset: null,
    });
  });

  it('renders recipient-safe provenance and routes to the native detail page', async () => {
    renderList();
    const link = await screen.findByRole('link', { name: /Release workflow/ });
    expect(link).toHaveAttribute('href', '/workflow/workflow-1');
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('Created')).toBeInTheDocument();
    expect(screen.getByText(/viewer/i)).toBeInTheDocument();
    expect(screen.queryByText(/tenant/i)).not.toBeInTheDocument();
    expect(listSharedResources).toHaveBeenCalledWith('workflow', 30, 0);
  });
});
