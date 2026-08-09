/**
 * UX-10e2 — TemplateNode rendered hover preview.
 *
 * Covers the pure path-rewrite helpers + the gating in NodeHoverCard:
 *   - a TemplateNode completed result `{ rendered: "<img src='/run/r1/a.png'>" }`
 *     signs the LOCAL path and renders a sandboxed iframe; an http(s) `<img>` is
 *     left untouched + never signed.
 *   - a non-template node (renderedHtml absent) shows the plain text preview
 *     (no iframe).
 *
 * The `sign` fn is INJECTED (dependency-injection) rather than `vi.mock`'d, so
 * this file is safe under vitest `isolate:false` (no shared-module-graph mock
 * clobbering of siblings — see feedback_vitest_isolate_false).
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, cleanup, screen, waitFor } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';
import en from '@/lib/i18n/locales/en.json';
import {
  parseRenderedResult,
  stripGradioWrapper,
  classifyMediaSrc,
  rewriteRenderedHtml,
} from '@/pages/canvas/nodes/template-preview-media';
import { TemplateHoverPreview } from '@/pages/canvas/nodes/TemplateHoverPreview';
import { NodeHoverCard } from '@/pages/canvas/nodes/NodeHoverCard';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: en } },
  interpolation: { escapeValue: false },
});

afterEach(() => cleanup());

describe('parseRenderedResult', () => {
  it('extracts `{ rendered, format }` from a `{ rendered, format }` dict', () => {
    expect(
      parseRenderedResult('{"rendered":"<p>hi</p>","format":"html"}'),
    ).toEqual({ rendered: '<p>hi</p>', format: 'html' });
    expect(
      parseRenderedResult('{"rendered":"## T","format":"markdown"}'),
    ).toEqual({ rendered: '## T', format: 'markdown' });
  });
  it('back-compat: no/empty/non-string format key → format: ""', () => {
    expect(parseRenderedResult('{"rendered":"<p>hi</p>"}')).toEqual({
      rendered: '<p>hi</p>',
      format: '',
    });
    expect(parseRenderedResult('{"rendered":"x","format":""}')).toEqual({
      rendered: 'x',
      format: '',
    });
    expect(parseRenderedResult('{"rendered":"x","format":123}')).toEqual({
      rendered: 'x',
      format: '',
    });
  });
  it('returns null for a plain (non-rendered) result or invalid JSON', () => {
    expect(parseRenderedResult('{"foo":1}')).toBeNull();
    expect(parseRenderedResult('not json')).toBeNull();
    expect(parseRenderedResult(undefined)).toBeNull();
    expect(parseRenderedResult('{"rendered":123}')).toBeNull();
  });
});

describe('stripGradioWrapper', () => {
  it('strips the /gradio_api/file= wrapper back to the raw path', () => {
    expect(stripGradioWrapper('/gradio_api/file=/run/r1/a.png')).toBe(
      '/run/r1/a.png',
    );
    expect(
      stripGradioWrapper('http://x/gradio_api/file=%2Frun%2Fr1%2Fa.png'),
    ).toBe('/run/r1/a.png');
  });
  it('leaves a non-wrapped path untouched', () => {
    expect(stripGradioWrapper('/run/r1/a.png')).toBe('/run/r1/a.png');
  });
});

describe('classifyMediaSrc', () => {
  it('classifies /run as a run-scoped local ref', () => {
    expect(classifyMediaSrc('/run/r1/a.png')).toEqual({
      path: '/run/r1/a.png',
      isRun: true,
      runId: 'r1',
    });
  });
  it('classifies /mount and /data as durable local refs', () => {
    expect(classifyMediaSrc('/mount/x.png')).toEqual({
      path: '/mount/x.png',
      isRun: false,
    });
    expect(classifyMediaSrc('/data/y.png')).toEqual({
      path: '/data/y.png',
      isRun: false,
    });
  });
  it('strips the gradio wrapper before classifying', () => {
    expect(classifyMediaSrc('/gradio_api/file=/run/r1/a.png')).toEqual({
      path: '/run/r1/a.png',
      isRun: true,
      runId: 'r1',
    });
  });
  it('passes http(s) + data URIs through (null = leave untouched)', () => {
    expect(classifyMediaSrc('https://cdn.example/a.png')).toBeNull();
    expect(classifyMediaSrc('http://x/a.png')).toBeNull();
    expect(classifyMediaSrc('data:image/png;base64,xxx')).toBeNull();
    expect(classifyMediaSrc('')).toBeNull();
  });
});

describe('rewriteRenderedHtml', () => {
  it('signs local paths (once each) + leaves http(s) untouched', async () => {
    const sign = vi
      .fn()
      .mockImplementation(async (a: { path: string }) => ({
        url: `signed://${a.path}`,
      }));
    const html =
      "<img src='/run/r1/a.png'>" +
      "<video src='/mount/v.mp4'><source src='/mount/v.mp4'></video>" +
      "<img src='https://cdn.example/keep.png'>" +
      '<p>hi</p>';

    const out = await rewriteRenderedHtml(html, {
      wfId: 'wf1',
      runId: 'r1',
      sign,
    });

    // /run path is run-scoped; /mount is resolved to the user's mount server-side.
    expect(sign).toHaveBeenCalledWith({ path: '/run/r1/a.png', run_id: 'r1' });
    expect(sign).toHaveBeenCalledWith({ path: '/mount/v.mp4', wf_id: 'wf1' });
    // /mount/v.mp4 appears twice (video + source) but is signed ONCE.
    expect(sign).toHaveBeenCalledTimes(2);

    expect(out).toContain('signed:///run/r1/a.png');
    expect(out).toContain('signed:///mount/v.mp4');
    // http(s) url is left as-is and never signed.
    expect(out).toContain('https://cdn.example/keep.png');
    expect(sign).not.toHaveBeenCalledWith(
      expect.objectContaining({ path: expect.stringContaining('cdn.example') }),
    );
  });
});

describe('TemplateHoverPreview (component)', () => {
  it('shows a loading state then renders a sandboxed iframe with signed src', async () => {
    const sign = vi.fn().mockResolvedValue({ url: 'signed://ok' });
    render(
      <I18nextProvider i18n={testI18n}>
        <TemplateHoverPreview
          rendered="<img src='/run/r1/a.png'><p>hi</p>"
          wfId="wf1"
          runId="r1"
          signFn={sign}
        />
      </I18nextProvider>,
    );
    // Loading first.
    expect(
      document.querySelector('[data-hover-template-loading]'),
    ).not.toBeNull();

    // Then the iframe.
    await waitFor(() => {
      expect(screen.getByTestId('template-preview-iframe')).toBeInTheDocument();
    });
    const iframe = screen.getByTestId(
      'template-preview-iframe',
    ) as HTMLIFrameElement;
    // sandbox="" with NO allow-scripts (primary XSS defense).
    expect(iframe.getAttribute('sandbox')).toBe('');
    const doc = iframe.getAttribute('srcdoc') ?? '';
    expect(doc).toContain('signed://ok');
    expect(doc).toContain('max-width: 100%'); // injected base style
    expect(sign).toHaveBeenCalledWith({ path: '/run/r1/a.png', run_id: 'r1' });
  });

  it('falls back (no iframe) when signing fails', async () => {
    const sign = vi.fn().mockRejectedValue(new Error('boom'));
    render(
      <I18nextProvider i18n={testI18n}>
        <TemplateHoverPreview rendered="<img src='/run/r1/a.png'>" signFn={sign} />
      </I18nextProvider>,
    );
    await waitFor(() => {
      expect(
        document.querySelector('[data-hover-template-error]'),
      ).not.toBeNull();
    });
    expect(screen.queryByTestId('template-preview-iframe')).toBeNull();
  });
});

describe('NodeHoverCard — rendered-preview gating', () => {
  it('shows the RAW result line (no iframe) for a completed TemplateNode', () => {
    // The hover card was reverted to always show the raw result dict — exactly
    // like every other node. The rendered preview now lives on the Run-node
    // panel's "Render" toggle, NOT the canvas hover card.
    render(
      <I18nextProvider i18n={testI18n}>
        <NodeHoverCard
          title="tmpl"
          typeLabel="Template"
          execState="completed"
          execResult='{"rendered":"<p>hi</p>"}'
          warnings={[]}
          suppressed={false}
          forceOpen
        >
          <div data-testid="body">b</div>
        </NodeHoverCard>
      </I18nextProvider>,
    );
    const el = document.querySelector('[data-hover-exec="completed"]')!;
    expect(el).not.toBeNull();
    expect(el.textContent).toContain('{"rendered":"<p>hi</p>"}');
    expect(screen.queryByTestId('template-preview-iframe')).toBeNull();
  });

  it('shows the plain text preview for a completed non-template node', () => {
    render(
      <I18nextProvider i18n={testI18n}>
        <NodeHoverCard
          title="code"
          typeLabel="Code"
          execState="completed"
          execResult="the answer is 42"
          warnings={[]}
          suppressed={false}
          forceOpen
        >
          <div data-testid="body">b</div>
        </NodeHoverCard>
      </I18nextProvider>,
    );
    const el = document.querySelector('[data-hover-exec="completed"]')!;
    expect(el.textContent).toContain('the answer is 42');
    expect(screen.queryByTestId('template-preview-iframe')).toBeNull();
  });
});
