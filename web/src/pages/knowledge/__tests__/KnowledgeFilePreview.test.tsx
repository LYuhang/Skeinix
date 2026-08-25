import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { KbFile } from '@/lib/api/kb';
import { KnowledgeFilePreview } from '@/pages/knowledge/KnowledgeFilePreview';

vi.mock('@/lib/api/kb', async (importOriginal) => {
  const original = await importOriginal<typeof import('@/lib/api/kb')>();
  return { ...original, getKbFileRaw: vi.fn() };
});

import { getKbFileRaw } from '@/lib/api/kb';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function file(
  name: string,
  mimeType: string,
  parserType: string,
): KbFile {
  return {
    id: `file-${name}`,
    name,
    parser_type: parserType,
    mime_type: mimeType,
    file_size: 12,
    status: parserType === 'binary' ? 'stored' : 'indexed',
    error_message: null,
    chunk_count: parserType === 'binary' ? 0 : 1,
    created_at: '2026-08-23T00:00:00Z',
    access: {
      capabilities: ['view'],
      effective_role: 'viewer',
      source: 'computed',
    },
    provenance: {
      ownership_scope: 'personal',
      owner: { type: 'user', display_name: 'Owner' },
      created_by: { type: 'user', display_name: 'Owner' },
      origin_type: 'uploaded',
    },
  };
}

function renderPreview(source: KbFile) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={testI18n}>
        <KnowledgeFilePreview kbId="kb-format-matrix" file={source} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe('KnowledgeFilePreview format routing', () => {
  beforeEach(() => {
    vi.mocked(getKbFileRaw).mockReset();
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:knowledge-preview'),
    });
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    });
  });

  afterEach(() => cleanup());

  it('renders Markdown and CSV as readable text', async () => {
    vi.mocked(getKbFileRaw).mockResolvedValueOnce(
      new Blob(['# Guide\n\nReadable note.'], { type: 'text/markdown' }),
    );
    const markdown = renderPreview(file('notes/guide.md', 'text/markdown', 'markdown'));
    expect(await screen.findByText('Readable note.')).toBeInTheDocument();
    markdown.unmount();

    vi.mocked(getKbFileRaw).mockResolvedValueOnce(
      new Blob(['name,value\nalpha,1\n'], { type: 'table/csv' }),
    );
    renderPreview(file('tables/metrics.csv', 'table/csv', 'csv'));
    expect(await screen.findByText(/alpha,1/)).toBeInTheDocument();
  });

  it('routes PDF, image, audio, and video to native read-only viewers', async () => {
    vi.mocked(getKbFileRaw).mockResolvedValue(new Blob(['raw']));

    const pdf = renderPreview(file('docs/report.pdf', 'application/pdf', 'pdf'));
    expect(await screen.findByTitle('docs/report.pdf')).toBeInTheDocument();
    pdf.unmount();

    const image = renderPreview(file('images/diagram.png', 'image/png', 'binary'));
    expect(await screen.findByRole('img', { name: 'images/diagram.png' })).toBeInTheDocument();
    image.unmount();

    const audio = renderPreview(file('media/brief.mp3', 'audio/mpeg', 'binary'));
    await waitFor(() => expect(audio.container.querySelector('audio')).not.toBeNull());
    audio.unmount();

    const video = renderPreview(file('media/demo.mp4', 'video/mp4', 'binary'));
    await waitFor(() => expect(video.container.querySelector('video')).not.toBeNull());
  });

  it('keeps PPTX available through the explicit download fallback', async () => {
    vi.mocked(getKbFileRaw).mockResolvedValue(new Blob(['PK'], {
      type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    }));
    renderPreview(file(
      'slides/deck.pptx',
      'application/vnd.openxmlformats-officedocument.presentationml.presentation',
      'pptx',
    ));
    expect(await screen.findByText('Preview is not available for this file type')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download deck.pptx' })).toHaveAttribute(
      'download',
      'deck.pptx',
    );
  });
});
