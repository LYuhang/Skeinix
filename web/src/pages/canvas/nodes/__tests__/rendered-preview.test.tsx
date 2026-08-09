/**
 * RenderedPreview — markdown image rendering + local VFS-path signing.
 *
 * Probe finding: react-markdown@10 DOES render an `<img>` for a plain http
 * markdown image (passthrough, unsigned) — so plain http images already work.
 * The bug was that markdown images pointing at LOCAL VFS paths (`/run|/mount|
 * /data`, or the engine's `/gradio_api/file=` wrapper) were never signed. The
 * fix routes markdown `![](…)` images through the same `useSignedMediaSrc`
 * signing the html path uses.
 *
 * The sign fn is INJECTED (dependency-injection via the `signFn` prop) rather
 * than `vi.mock`'d, so this file is safe under vitest `isolate:false` — no
 * shared-module-graph mock clobbering of siblings (see
 * feedback_vitest_isolate_false). Mirrors template-hover-preview.test.tsx.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, cleanup, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';
import en from '@/lib/i18n/locales/en.json';
import { RenderedPreview } from '@/pages/canvas/nodes/RenderedPreview';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: en } },
  interpolation: { escapeValue: false },
});

/** A sign fn that echoes the path so assertions can check it was used. */
function makeSign() {
  return vi.fn(async (a: { path: string }) => ({ url: `signed://${a.path}` }));
}

function renderPreview(
  props: {
    rendered: string;
    format: string;
    wfId?: string;
    runId?: string;
  },
  signFn?: ReturnType<typeof makeSign>,
) {
  return render(
    <I18nextProvider i18n={testI18n}>
      <RenderedPreview {...props} signFn={signFn} />
    </I18nextProvider>,
  );
}

afterEach(() => cleanup());

describe('RenderedPreview markdown — probe: http image', () => {
  it('renders an <img> for an http markdown image (passthrough, no signing)', async () => {
    const sign = makeSign();
    renderPreview(
      { format: 'markdown', rendered: '![alt](https://example.com/x.png)' },
      sign,
    );
    await waitFor(() => {
      const img = document.querySelector('img');
      expect(img).not.toBeNull();
      expect(img!.getAttribute('src')).toBe('https://example.com/x.png');
    });
    expect(sign).not.toHaveBeenCalled();
  });
});

describe('RenderedPreview markdown — local VFS-path signing', () => {
  it('signs a /gradio_api/file=/run/... markdown image src', async () => {
    const sign = makeSign();
    renderPreview(
      {
        format: 'markdown',
        rendered: '![](/gradio_api/file=/run/r1/a.png)',
        wfId: 'w',
        runId: 'r1',
      },
      sign,
    );
    await waitFor(() => {
      const img = document.querySelector('img');
      expect(img).not.toBeNull();
      expect(img!.getAttribute('src')).toBe('signed:///run/r1/a.png');
    });
    expect(sign).toHaveBeenCalledWith({ path: '/run/r1/a.png', run_id: 'r1' });
  });

  it('signs a raw /mount/... markdown image src', async () => {
    const sign = makeSign();
    renderPreview(
      {
        format: 'markdown',
        rendered: '![](/mount/pic.png)',
        wfId: 'w',
        runId: 'r1',
      },
      sign,
    );
    await waitFor(() => {
      const img = document.querySelector('img');
      expect(img!.getAttribute('src')).toBe('signed:///mount/pic.png');
    });
    expect(sign).toHaveBeenCalledWith({ path: '/mount/pic.png', wf_id: 'w' });
  });

  it('still renders headings/lists (gfm) alongside images', async () => {
    renderPreview({
      format: 'markdown',
      rendered: '# Title\n\n- one\n- two\n',
    });
    await waitFor(() => {
      expect(screen.getByText('Title')).toBeInTheDocument();
      expect(screen.getByText('one')).toBeInTheDocument();
    });
  });
});
