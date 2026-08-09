import { describe, expect, it } from 'vitest';
import {
  buildInteractiveHtmlDocument,
  interactiveBootstrapScriptSource,
  INTERACTIVE_SANDBOX_CHANNEL,
  isInteractiveSandboxMessage,
} from '@/components/agent-sidebar/tool-render/interactive-html-runtime';

describe('interactive HTML runtime', () => {
  it('keeps executable bootstrap text invariant while escaping dynamic capability data', () => {
    const first = buildInteractiveHtmlDocument({
      artifactId: 'ia_first',
      html: '<main>first</main>',
      resourceMounts: [{
        path_prefix: '/data/',
        root_url: 'https://api.test/capability/first/',
      }],
      baseUrl: 'https://api.test/capability/first/data/',
      initialState: { fields: { label: '</script><script>alert(1)</script>' } },
      frozen: false,
    });
    const second = buildInteractiveHtmlDocument({
      artifactId: 'ia_second',
      html: '<main>second</main>',
      resourceMounts: [],
      baseUrl: 'https://api.test/capability/second/data/',
      initialState: {},
      frozen: true,
    });
    const extract = (document: string) => document.match(
      /<script data-vibecanvas-interactive-runtime[^>]*>([\s\S]*?)<\/script>/,
    )?.[1];

    expect(extract(first)).toBe(interactiveBootstrapScriptSource());
    expect(extract(second)).toBe(interactiveBootstrapScriptSource());
    expect(first).toContain('data-vibecanvas-interactive-runtime');
    expect(first).toContain('&lt;/script&gt;&lt;script&gt;alert(1)&lt;/script&gt;');
    expect(first).not.toContain('</script><script>alert(1)</script>');
  });

  it('injects an isolated resource base without changing persisted Agent paths', () => {
    const original = [
      '<form>',
      '<img src="/data/dataset/1.png">',
      '<input name="sample-1.label">',
      '<button type="submit">Submit</button>',
      '<script>fetch("/data/dataset/items.json")</script>',
      '<script>fetch("/mount/data/data_list.jsonl").then(r => r.text())</script>',
      '<script>save.onclick=()=>fetch("/data/labels.json",{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(labels)})</script>',
      '<script>const p = "/mount/data/" + item.image; img.src = p</script>',
      '<script>style.setProperty("background-image", "url(/mount/data/bg.png)")</script>',
      '</form>',
    ].join('');
    const rendered = buildInteractiveHtmlDocument({
      artifactId: 'ia_test',
      html: original,
      resourceMounts: [
        {
          path_prefix: '/mount/',
          root_url: 'https://api.test/api/v1/vfs/resources/mount-opaque/',
        },
        {
          path_prefix: '/',
          root_url: 'https://api.test/api/v1/vfs/resources/opaque/',
        },
      ],
      baseUrl: 'https://api.test/api/v1/vfs/resources/opaque/data/',
      initialState: { fields: { 'sample-1.label': 'pass' } },
      frozen: false,
    });

    expect(original).toContain('/data/dataset/1.png');
    expect(rendered).toContain('<base href="https://api.test/api/v1/vfs/resources/opaque/data/">');
    expect(rendered).toContain('<meta name="referrer" content="no-referrer">');
    expect(rendered).toContain('img-src data: blob: https://api.test/api/v1/vfs/resources/');
    expect(rendered).toContain('media-src data: blob: https://api.test/api/v1/vfs/resources/');
    expect(rendered).not.toContain('img-src data: blob: http: https:');
    expect(rendered).toContain('connect-src data: blob: https://api.test/api/v1/vfs/resources/');
    expect(rendered).toContain("script-src 'unsafe-inline'");
    expect(rendered).not.toContain("'unsafe-eval'");
    expect(rendered).not.toContain("script-src 'unsafe-inline' http:");
    expect(rendered).toContain("frame-src 'none'");
    expect(rendered).toContain("worker-src 'none'");
    expect(rendered).not.toContain('allow-same-origin');
    expect(rendered).toContain("window.fetch =");
    expect(rendered).toContain('if (!USER_ACTIVATION.isActive || Date.now() > writeArmedUntil)');
    expect(rendered).toContain("emit('ready');");
    expect(rendered).toContain("message.type !== 'vfs.write.result'");
    expect(rendered).toContain("path.startsWith('/data/')");
    expect(rendered).toContain("emit('draft', collect(), { flush: true })");
    expect(rendered).toContain('WRITE_GESTURE_WINDOW_MS');
    expect(rendered).toContain("method === 'PUT' || method === 'POST'");
    expect(rendered).toContain("document.addEventListener('submit'");
    expect(rendered).toContain("emit('preview.open'");
    expect(rendered).toContain('sample-1.label');
    expect(rendered).toContain('&quot;path_prefix&quot;:&quot;/mount/&quot;');
    expect(rendered).toContain('&quot;path_prefix&quot;:&quot;/&quot;');
    expect(rendered).toContain('VFS_MOUNTS.find');
    expect(rendered).toContain('candidate.slice(mount.path_prefix.length)');
    expect(rendered).toContain('CSSStyleDeclaration.prototype.setProperty');
    expect(rendered).toContain('CSSStyleSheet.prototype.insertRule');
    expect(rendered).toContain("Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML')");
    expect(rendered).toContain('Element.prototype.insertAdjacentHTML');
    expect(rendered).toContain("window.addEventListener('error'");
    expect(rendered).toContain("window.addEventListener('unhandledrejection'");
    expect(rendered).toContain('DIAGNOSTIC_TIMEOUT_MS');
    expect(rendered).toContain("input instanceof Request\n          ? input.url");
  });

  it('accepts only the narrow postMessage protocol', () => {
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'submit',
      state: { fields: { label: 'pass' } },
    })).toBe(false);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'draft',
      flush: true,
      state: { fields: { label: 'kept' } },
    })).toBe(true);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'draft',
      flush: 'yes',
      state: {},
    })).toBe(false);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'vfs.write',
      requestId: 'write-1',
      path: '/data/labels.json',
      method: 'PUT',
      content: '{"label":"pass"}',
      contentType: 'application/json',
    })).toBe(true);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'vfs.write',
      requestId: 'write-escape',
      path: '/data/../mount/private.txt',
      method: 'PUT',
      content: 'nope',
      contentType: 'text/plain',
    })).toBe(false);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'preview.open',
      path: '/data/report.pdf',
    })).toBe(true);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'preview.open',
      path: '/etc/passwd',
    })).toBe(false);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'vfs.write',
      requestId: 'write-1',
      path: '/data/labels.json',
      method: 'DELETE',
      content: '',
      contentType: 'application/json',
    })).toBe(false);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'navigate-parent',
    })).toBe(false);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'diagnostic',
      diagnostic: {
        id: 'fetch:1',
        status: 'open',
        severity: 'error',
        kind: 'fetch',
        message: 'HTTP 404',
        path: '/mount/data/data.jsonl',
        httpStatus: 404,
      },
    })).toBe(true);
    expect(isInteractiveSandboxMessage({
      channel: INTERACTIVE_SANDBOX_CHANNEL,
      artifactId: 'ia_1',
      sessionNonce: 'nonce-1',
      type: 'diagnostic',
      diagnostic: { id: 'bad', status: 'open', kind: 'fetch', message: 'missing severity' },
    })).toBe(false);
  });
});
