import type { ReactElement } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';
import { MemoryRouter, Route, Routes } from 'react-router';
import type { ResourceAccess } from '@/lib/api/organizations';
import type { KbListItem } from '@/lib/api/kb';

vi.mock('@/lib/api/kb', () => ({
  createKb: vi.fn(),
  deleteKb: vi.fn(),
  deleteKbFile: vi.fn(),
  getKbFileContent: vi.fn(),
  getKb: vi.fn(),
  listKbFiles: vi.fn(),
  listKbs: vi.fn(),
  reindexKbFile: vi.fn(),
  uploadKbFile: vi.fn(),
}));

import {
  deleteKb,
  deleteKbFile,
  getKbFileContent,
  getKb,
  listKbFiles,
  listKbs,
} from '@/lib/api/kb';
import { KnowledgeDetailPage } from '@/pages/knowledge/KnowledgeDetailPage';
import { KnowledgeListPage } from '@/pages/knowledge/KnowledgeListPage';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderPage(ui: ReactElement, initialEntry = '/knowledge') {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={testI18n}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <Routes>
            <Route path="/knowledge" element={ui} />
            <Route path="/knowledge/:kbId" element={ui} />
          </Routes>
        </MemoryRouter>
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const managerAccess = {
  capabilities: ['view_metadata', 'view', 'update', 'use', 'manage_access', 'delete'],
  effective_role: 'manager',
  source: 'computed',
} satisfies ResourceAccess;

const detail = {
  id: 'kb-1',
  name: 'Product handbook',
  description: 'Policies and release notes',
  retrieval_strategy: 'agentic_lexical',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
  latest_updated_at: '2026-08-01T00:00:00Z',
  file_count: 2,
  chunk_count: 12,
  pending_count: 0,
  indexing_count: 0,
  indexed_count: 1,
  failed_count: 1,
  access: managerAccess,
} satisfies KbListItem;

describe('Knowledge pages', () => {
  beforeEach(() => {
    vi.mocked(listKbs).mockReset();
    vi.mocked(getKb).mockReset();
    vi.mocked(getKbFileContent).mockReset();
    vi.mocked(getKbFileContent).mockResolvedValue({
      file_id: 'file-1',
      file_name: 'handbook.pdf',
      parser_type: 'pdf',
      status: 'indexed',
      offset: 0,
      next_offset: 0,
      total_chunks: 0,
      has_more: false,
      chunks: [],
    });
    vi.mocked(listKbFiles).mockReset();
    vi.mocked(deleteKb).mockReset();
    vi.mocked(deleteKbFile).mockReset();
  });

  it('renders searchable knowledge rows and the dedicated empty state', async () => {
    vi.mocked(listKbs).mockResolvedValue([detail]);
    const user = userEvent.setup();
    renderPage(<KnowledgeListPage />);

    expect(await screen.findByText('Product handbook')).toBeInTheDocument();
    expect(screen.getByText('Needs attention')).toBeInTheDocument();
    await user.click(screen.getByRole('combobox', { name: 'Filter by status' }));
    await user.click(screen.getByRole('option', { name: 'Ready' }));
    expect(screen.getByText('No matching knowledge bases')).toBeInTheDocument();

    await user.click(screen.getByRole('combobox', { name: 'Filter by status' }));
    await user.click(screen.getByRole('option', { name: 'All statuses' }));
    await user.type(screen.getByPlaceholderText('Search knowledge bases'), 'missing');
    expect(screen.getByText('No matching knowledge bases')).toBeInTheDocument();
  });

  it('shows index health, partial failure, and Agent-readable file content', async () => {
    vi.mocked(getKb).mockResolvedValue(detail);
    vi.mocked(listKbFiles).mockResolvedValue([
      {
        id: 'file-1',
        name: 'handbook.pdf',
        parser_type: 'pdf',
        file_size: 1024,
        status: 'indexed',
        error_message: null,
        chunk_count: 12,
        created_at: '2026-08-01T00:00:00Z',
        access: detail.access,
      },
      {
        id: 'file-2',
        name: 'broken.txt',
        parser_type: 'text',
        file_size: 20,
        status: 'failed',
        error_message: 'Unsupported encoding',
        chunk_count: 0,
        created_at: '2026-08-01T00:00:00Z',
        access: detail.access,
      },
    ]);
    vi.mocked(getKbFileContent).mockResolvedValue({
      file_id: 'file-1',
      file_name: 'handbook.pdf',
      parser_type: 'pdf',
      status: 'indexed',
      offset: 0,
      next_offset: 1,
      total_chunks: 1,
      has_more: false,
      chunks: [{ index: 0, text: 'Release trains run every Tuesday.' }],
    });
    const user = userEvent.setup();
    renderPage(<KnowledgeDetailPage />, '/knowledge/kb-1');

    expect(await screen.findByText('Product handbook')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Overview' })).toHaveAttribute('data-state', 'active');
    expect(screen.queryByText('How this knowledge base is used')).not.toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /Sources/ }));
    expect(screen.getByRole('tree', { name: 'Source files' })).toBeInTheDocument();
    expect(screen.getByText('1 source files need attention')).toBeInTheDocument();
    expect(screen.getByText('Unsupported encoding', { exact: false })).toBeInTheDocument();
    await user.click(screen.getByRole('treeitem', { name: /handbook.pdf/ }));
    expect(await screen.findByText('Release trains run every Tuesday.')).toBeInTheDocument();
    expect(getKbFileContent).toHaveBeenCalledWith('kb-1', 'file-1', 0, 50);
    await user.type(screen.getByPlaceholderText('Search source files'), 'broken');
    expect(screen.queryByText('handbook.pdf')).not.toBeInTheDocument();
    expect(screen.getAllByText('broken.txt').length).toBeGreaterThan(0);
    expect(screen.queryByRole('tab', { name: 'Retrieval' })).not.toBeInTheDocument();
  });

  it('requires confirmation before deleting an indexed source', async () => {
    vi.mocked(getKb).mockResolvedValue(detail);
    vi.mocked(listKbFiles).mockResolvedValue([{
      id: 'file-1',
      name: 'handbook.pdf',
      parser_type: 'pdf',
      file_size: 1024,
      status: 'indexed',
      error_message: null,
      chunk_count: 12,
      created_at: '2026-08-01T00:00:00Z',
      access: detail.access,
    }]);
    vi.mocked(deleteKbFile).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage(<KnowledgeDetailPage />, '/knowledge/kb-1?tab=sources');

    expect((await screen.findAllByText('handbook.pdf')).length).toBeGreaterThan(0);
    expect(screen.getByRole('tab', { name: /Sources/ })).toHaveAttribute('data-state', 'active');
    await user.click(screen.getByRole('button', { name: 'Delete handbook.pdf' }));
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.getByText('handbook.pdf and its indexed chunks will be removed from retrieval.')).toBeInTheDocument();
    expect(vi.mocked(deleteKbFile)).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(deleteKbFile).toHaveBeenCalledWith('kb-1', 'file-1'));
  });

  it('requires confirmation before deleting a knowledge base', async () => {
    vi.mocked(getKb).mockResolvedValue(detail);
    vi.mocked(listKbFiles).mockResolvedValue([]);
    vi.mocked(deleteKb).mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage(<KnowledgeDetailPage />, '/knowledge/kb-1');

    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
    expect(screen.getByText('Product handbook and all of its source files will be removed from active retrieval.')).toBeInTheDocument();
    expect(vi.mocked(deleteKb)).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Delete' }));
    await waitFor(() => expect(deleteKb).toHaveBeenCalledWith('kb-1'));
  });
});
