import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { PreviewDescriptorV1 } from '@/lib/preview/protocol';
import { DocxPreviewRenderer } from '../DocxPreviewRenderer';
import { prepareDocxPages } from '../docx-page-layout';

const renderAsync = vi.fn();

vi.mock('docx-preview', () => ({ renderAsync }));

const descriptor = {
  schemaVersion: 1,
  fileRef: {
    schemaVersion: 1,
    scope: 'chat',
    chatId: 'chat-1',
    path: '/data/report.docx',
  },
  name: 'report.docx',
  sizeBytes: 2048,
  contentType: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  detectedType: 'docx',
  revision: 'rev-1',
  renderer: 'docx',
  loadPolicy: 'inline',
  capabilities: { preview: true, edit: false, download: true },
  content: { url: '/api/v1/vfs/raw?sig=one', truncated: false, rangeSupported: true },
} satisfies PreviewDescriptorV1;

beforeEach(() => {
  renderAsync.mockReset();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    blob: () => Promise.resolve(new Blob(['docx'])),
  }));
  vi.stubGlobal('ResizeObserver', class {
    observe() {}

    disconnect() {}
  });
  renderAsync.mockImplementation(async (_blob, body: HTMLElement, styles: HTMLElement) => {
    styles.append(document.createElement('style'));
    const wrapper = document.createElement('div');
    wrapper.className = 'docx-wrapper';
    wrapper.append(document.createElement('section'), document.createElement('section'));
    for (const page of wrapper.querySelectorAll('section')) page.className = 'docx';
    body.append(wrapper);
  });
});

describe('DocxPreviewRenderer', () => {
  it('renders document pages into a continuous paper canvas', async () => {
    render(
      <DocxPreviewRenderer
        descriptor={descriptor}
        loadAllowed
        onDirtyChange={vi.fn()}
      />,
    );

    await waitFor(() => expect(renderAsync).toHaveBeenCalledOnce());
    expect(await screen.findByText('2 pages')).toBeInTheDocument();
    const canvas = screen.getByTestId('docx-document-canvas');
    const pages = canvas.querySelectorAll<HTMLElement>('[data-preview-page="true"]');
    expect(pages).toHaveLength(2);
    expect(pages[0]).toHaveStyle({ width: '816px', minHeight: '1056px' });
    expect(renderAsync).toHaveBeenCalledWith(
      expect.any(Blob),
      canvas,
      expect.any(HTMLElement),
      expect.objectContaining({
        ignoreLastRenderedPageBreak: false,
        experimental: true,
      }),
    );
  });

  it('preserves explicit OOXML page geometry while fitting the viewer width', () => {
    const body = document.createElement('div');
    const wrapper = document.createElement('div');
    wrapper.className = 'docx-wrapper';
    const page = document.createElement('section');
    page.className = 'docx';
    page.style.width = '612pt';
    page.style.minHeight = '792pt';
    page.style.padding = '72pt';
    wrapper.append(page);
    body.append(wrapper);
    vi.spyOn(page, 'getBoundingClientRect').mockReturnValue({
      x: 0,
      y: 0,
      width: 816,
      height: 1056,
      top: 0,
      left: 0,
      right: 816,
      bottom: 1056,
      toJSON: () => ({}),
    });

    const result = prepareDocxPages(body, 424);

    expect(page.style.width).toBe('612pt');
    expect(page.style.minHeight).toBe('792pt');
    expect(page.style.padding).toBe('72pt');
    expect(result.scale).toBeCloseTo(392 / 816);
    expect(wrapper.style.zoom).toBe(String(result.scale));
  });
});
