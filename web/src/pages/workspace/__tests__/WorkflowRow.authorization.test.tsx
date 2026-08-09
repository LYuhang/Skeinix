import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import i18n from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { fixtureWorkflow } from '@/__tests__/msw-handlers';
import { TooltipProvider } from '@/components/ui/tooltip';
import type { ResourceAction } from '@/lib/api/organizations';
import { WorkflowRow } from '@/pages/workspace/WorkflowRow';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderRow(capabilities: ResourceAction[]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const workflow = fixtureWorkflow({
    access: {
      capabilities,
      effective_role: capabilities.includes('view') ? 'viewer' : null,
      source: 'computed',
    },
  });

  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={testI18n}>
        <TooltipProvider>
          <MemoryRouter>
            <table>
              <tbody>
                <WorkflowRow
                  wf={workflow}
                  onEdit={vi.fn()}
                  onDelete={vi.fn()}
                  onDuplicate={vi.fn()}
                  duplicating={false}
                />
              </tbody>
            </table>
          </MemoryRouter>
        </TooltipProvider>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe('<WorkflowRow> authorization projection', () => {
  it('renders metadata without a content link for auditor-style access', () => {
    renderRow(['view_metadata']);

    expect(screen.getByTestId('wf-row-metadata-only')).toHaveTextContent(
      'Test Workflow',
    );
    expect(screen.queryByRole('link', { name: 'Test Workflow' })).toBeNull();
    expect(screen.getByTestId('wf-row-open')).toBeDisabled();
  });

  it('keeps the normal workflow links for an explicitly authorized viewer', () => {
    renderRow(['view_metadata', 'view']);

    expect(screen.getByRole('link', { name: 'Test Workflow' })).toHaveAttribute(
      'href',
      '/workflow/wf_test_1',
    );
    expect(screen.getByTestId('wf-row-open')).toBeEnabled();
  });
});
