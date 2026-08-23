import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ActionableError } from '@/components/presentation/ActionableError';
import { CompactEmptyState } from '@/components/presentation/CompactEmptyState';
import { ExecutionThread } from '@/components/presentation/ExecutionThread';
import { FileTypeIcon } from '@/components/presentation/FileTypeIcon';
import { ResourceIcon } from '@/components/presentation/ResourceIcon';
import { fileVisualFor } from '@/lib/presentation/file-visuals';
import { resourceVisual } from '@/lib/presentation/resource-visuals';
import { setLocale } from '@/lib/i18n';

beforeEach(() => {
  setLocale('en');
});

describe('workbench presentation primitives', () => {
  it('keeps resource identity separate from execution status', () => {
    expect(resourceVisual('workflow').foregroundClass).toBe('text-resource-workflow');
    render(<ResourceIcon kind="workflow" label="Workflow" />);
    expect(screen.getByRole('img', { name: 'Workflow' })).toHaveClass('bg-resource-workflow/10');
  });

  it('classifies familiar office, code, media, archive, and folder files', () => {
    expect(fileVisualFor({ fileName: 'report.pdf' }).kind).toBe('pdf');
    expect(fileVisualFor({ fileName: 'forecast.xlsx' }).kind).toBe('spreadsheet');
    expect(fileVisualFor({ fileName: 'brief.pptx' }).kind).toBe('presentation');
    expect(fileVisualFor({ fileName: 'agent.ts' }).kind).toBe('code');
    expect(fileVisualFor({ fileName: 'demo.mp4' }).kind).toBe('video');
    expect(fileVisualFor({ fileName: 'bundle.zip' }).kind).toBe('archive');
    expect(fileVisualFor({ directory: true }).kind).toBe('folder');

    render(<FileTypeIcon fileName="forecast.xlsx" label="Spreadsheet" />);
    expect(screen.getByRole('img', { name: 'Spreadsheet' })).toHaveAttribute(
      'data-file-kind',
      'spreadsheet',
    );
  });

  it('keeps technical errors collapsed behind an actionable summary', () => {
    const retry = vi.fn();
    render(
      <ActionableError
        title="Could not connect"
        description="Check the service address and credentials."
        actionLabel="Test again"
        onAction={retry}
        technicalDetails="ExceptionGroup: internal transport failed"
        requestId="request-1"
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Could not connect');
    const details = screen.getByText('Technical details').closest('details');
    expect(details).not.toHaveAttribute('open');
    fireEvent.click(screen.getByRole('button', { name: 'Test again' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('localizes the ActionableError technical-details fallback', () => {
    setLocale('zh');
    render(
      <ActionableError
        title="连接失败"
        technicalDetails="Internal diagnostics"
      />,
    );

    expect(screen.getByText('技术详情')).toBeInTheDocument();
    expect(screen.queryByText('Technical details')).not.toBeInTheDocument();
  });

  it('renders a compact empty state with one clear action', () => {
    const create = vi.fn();
    render(
      <CompactEmptyState
        title="No workflows yet"
        description="Create one to begin."
        actionLabel="Create workflow"
        onAction={create}
      />,
    );
    expect(screen.getByText('No workflows yet').closest('section')).toHaveClass('min-h-32');
    fireEvent.click(screen.getByRole('button', { name: 'Create workflow' }));
    expect(create).toHaveBeenCalledTimes(1);
  });

  it('expresses ordered execution using text and icons, not color alone', () => {
    render(
      <ExecutionThread
        items={[
          { id: 'search', title: 'Search references', status: 'success', meta: '1.2s' },
          { id: 'build', title: 'Build workflow', status: 'running' },
        ]}
      />,
    );
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText('Search references')).toBeInTheDocument();
    expect(screen.getByText('Build workflow')).toBeInTheDocument();
  });
});
