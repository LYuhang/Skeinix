import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import { EntityDetailShell } from '@/components/layout/entity-detail-shell';
import {
  ManagementPageShell,
  ManagementToolbar,
} from '@/components/layout/management-page-shell';
import { OperationalSummary } from '@/components/layout/operational-summary';

describe('page archetype contracts', () => {
  it('gives list pages stable header, action, toolbar, and content regions', () => {
    const { container } = render(
      <ManagementPageShell
        title="Workflows"
        description="Build and manage reusable workflows."
        actions={<button type="button">New workflow</button>}
      >
        <ManagementToolbar>
          <label>
            Search
            <input />
          </label>
        </ManagementToolbar>
        <div>Workflow list</div>
      </ManagementPageShell>,
    );

    expect(container.querySelector('[data-page-archetype="list-index"]')).toBeTruthy();
    expect(container.querySelector('[data-page-region="header"]')).toBeTruthy();
    expect(container.querySelector('[data-page-region="primary-actions"]')).toHaveTextContent('New workflow');
    expect(container.querySelector('[data-page-region="toolbar"]')).toHaveTextContent('Search');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Workflows');
  });

  it('keeps continuous details under one identity header and content surface', () => {
    const { container } = render(
      <MemoryRouter>
        <EntityDetailShell
          backTo="/tasks"
          backLabel="Back to tasks"
          title="Import customers"
          status={<span>Running</span>}
          metadata={<span>Updated just now</span>}
        >
          <section>Execution overview</section>
        </EntityDetailShell>
      </MemoryRouter>,
    );

    expect(container.querySelector('[data-page-archetype="continuous-detail"]')).toBeTruthy();
    expect(container.querySelector('[data-page-region="identity"]')).toHaveTextContent(
      'Import customersRunningUpdated just now',
    );
    expect(container.querySelector('[data-page-region="detail-content"]')).toHaveTextContent(
      'Execution overview',
    );
    expect(screen.getByRole('link', { name: 'Back to tasks' })).toHaveAttribute('href', '/tasks');
  });

  it('renders operational summaries as one labelled region', () => {
    render(
      <OperationalSummary
        label="Deployment status summary"
        items={[
          { label: 'Active', value: 3, tone: 'success' },
          { label: 'Disabled', value: 1, tone: 'neutral' },
        ]}
      />,
    );

    expect(screen.getByRole('region', { name: 'Deployment status summary' })).toHaveTextContent(
      'Active3Disabled1',
    );
  });
});
