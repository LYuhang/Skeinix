export const INTERACTIVE_SANDBOX_CHANNEL = 'vibecanvas:interactive:v1';

export interface InteractiveSandboxMessage {
  channel: typeof INTERACTIVE_SANDBOX_CHANNEL;
  artifactId: string;
  type: 'draft' | 'ready' | 'diagnostic' | 'vfs.write' | 'preview.open';
  sessionNonce: string;
  state?: Record<string, unknown>;
  diagnostic?: InteractiveRenderDiagnostic;
  requestId?: string;
  path?: string;
  method?: 'PUT' | 'POST';
  content?: string;
  contentType?: string;
  flush?: boolean;
}

export interface InteractiveRenderDiagnostic {
  id: string;
  status: 'open' | 'resolved';
  severity: 'warning' | 'error';
  kind: 'boot' | 'script' | 'promise' | 'fetch' | 'xhr' | 'resource' | 'contract';
  message: string;
  path?: string;
  httpStatus?: number;
  line?: number;
  column?: number;
}

export interface InteractiveHtmlResourceMount {
  path_prefix: string;
  root_url: string;
}

function isSafeVfsPath(
  value: unknown,
  { writable = false }: { writable?: boolean } = {},
): value is string {
  if (typeof value !== 'string' || value.length > 2048 || value.includes('\\')) return false;
  const path = value.split(/[?#]/, 1)[0];
  const allowed = writable
    ? path.startsWith('/data/')
    : (
      path.startsWith('/data/')
      || path.startsWith('/mount/')
      || path.startsWith('/run/')
    );
  return allowed
    && !path.includes('\0')
    && !path.split('/').some((segment) => segment === '.' || segment === '..');
}

export function isInteractiveSandboxMessage(value: unknown): value is InteractiveSandboxMessage {
  if (!value || typeof value !== 'object') return false;
  const message = value as Record<string, unknown>;
  const baseValid = message.channel === INTERACTIVE_SANDBOX_CHANNEL
    && typeof message.artifactId === 'string'
    && typeof message.sessionNonce === 'string';
  if (!baseValid) return false;
  if (message.type === 'diagnostic') {
    if (!message.diagnostic || typeof message.diagnostic !== 'object' || Array.isArray(message.diagnostic)) {
      return false;
    }
    const diagnostic = message.diagnostic as Record<string, unknown>;
    return typeof diagnostic.id === 'string'
      && (diagnostic.status === 'open' || diagnostic.status === 'resolved')
      && (diagnostic.severity === 'warning' || diagnostic.severity === 'error')
      && ['boot', 'script', 'promise', 'fetch', 'xhr', 'resource', 'contract'].includes(String(diagnostic.kind))
      && typeof diagnostic.message === 'string'
      && (diagnostic.path === undefined || typeof diagnostic.path === 'string')
      && (diagnostic.httpStatus === undefined || typeof diagnostic.httpStatus === 'number')
      && (diagnostic.line === undefined || typeof diagnostic.line === 'number')
      && (diagnostic.column === undefined || typeof diagnostic.column === 'number');
  }
  if (message.type === 'vfs.write') {
    return typeof message.requestId === 'string'
      && message.requestId.length <= 200
      && isSafeVfsPath(message.path, { writable: true })
      && (message.method === 'PUT' || message.method === 'POST')
      && typeof message.content === 'string'
      && message.content.length <= 10 * 1024 * 1024
      && typeof message.contentType === 'string';
  }
  if (message.type === 'preview.open') {
    return isSafeVfsPath(message.path);
  }
  return (message.type === 'draft' || message.type === 'ready')
    && (message.flush === undefined || typeof message.flush === 'boolean')
    && (message.state === undefined || (
      typeof message.state === 'object' && message.state !== null && !Array.isArray(message.state)
    ));
}

function escapeHtmlAttribute(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

export function buildInteractiveHtmlDocument({
  artifactId,
  html,
  resourceMounts,
  baseUrl,
  initialState,
  frozen,
}: {
  artifactId: string;
  html: string;
  resourceMounts: InteractiveHtmlResourceMount[];
  baseUrl: string;
  initialState: Record<string, unknown>;
  frozen: boolean;
}): string {
  const mounts = resourceMounts
    .map((mount) => ({
      path_prefix: (mount.path_prefix.startsWith('/')
        ? mount.path_prefix
        : '/' + mount.path_prefix).replace(/\/*$/, '/'),
      root_url: mount.root_url.endsWith('/') ? mount.root_url : `${mount.root_url}/`,
    }))
    .sort((left, right) => right.path_prefix.length - left.path_prefix.length);
  const base = baseUrl.endsWith('/') ? baseUrl : `${baseUrl}/`;
  const resourceSources = Array.from(new Set(mounts.map((mount) => mount.root_url))).join(' ');
  const csp = [
    "default-src 'none'",
    // Agent-authored inline behavior is the Interactive contract. External
    // code, eval, workers, nested frames, and arbitrary data channels are not.
    "script-src 'unsafe-inline'",
    "style-src 'unsafe-inline'",
    `img-src data: blob: ${resourceSources}`,
    `media-src data: blob: ${resourceSources}`,
    `font-src data: blob: ${resourceSources}`,
    `connect-src data: blob: ${resourceSources}`,
    "worker-src 'none'",
    "object-src 'none'",
    "frame-src 'none'",
    "child-src 'none'",
    "form-action 'none'",
  ].join('; ');
  const bootstrapConfig = escapeHtmlAttribute(JSON.stringify({
    channel: INTERACTIVE_SANDBOX_CHANNEL,
    artifactId,
    mounts,
    initialState,
    frozen,
  }));

  const bootstrap = `
<meta http-equiv="Content-Security-Policy" content="${escapeHtmlAttribute(csp)}">
<meta name="referrer" content="no-referrer">
<base href="${escapeHtmlAttribute(base)}">
<script data-vibecanvas-interactive-runtime data-config="${bootstrapConfig}">
(() => {
  'use strict';
  const runtimeScript = document.currentScript;
  const runtimeConfig = JSON.parse(runtimeScript?.getAttribute('data-config') || '{}');
  const CHANNEL = String(runtimeConfig.channel || '');
  const ARTIFACT_ID = String(runtimeConfig.artifactId || '');
  const VFS_MOUNTS = Array.isArray(runtimeConfig.mounts) ? runtimeConfig.mounts : [];
  const INITIAL_STATE = runtimeConfig.initialState && typeof runtimeConfig.initialState === 'object'
    ? runtimeConfig.initialState
    : {};
  const FROZEN = runtimeConfig.frozen === true;
  const SESSION_NONCE = crypto.randomUUID();
  const USER_ACTIVATION = navigator.userActivation;
  const RESOURCE_ATTRS = new Set(['src', 'poster', 'href']);
  const VFS_RESOURCE_ORIGINS = new Set(
    VFS_MOUNTS.map((mount) => new URL(mount.root_url).origin),
  );
  const postToParent = window.parent.postMessage.bind(window.parent);
  const DIAGNOSTIC_TIMEOUT_MS = 10_000;
  const WRITE_GESTURE_WINDOW_MS = 10_000;
  const pendingWrites = new Map();
  let diagnosticSequence = 0;
  let writeSequence = 0;
  let writeArmedUntil = 0;

  const emit = (type, state, extra = {}) => {
    postToParent({
      channel: CHANNEL,
      artifactId: ARTIFACT_ID,
      sessionNonce: SESSION_NONCE,
      type,
      state,
      ...extra,
    }, '*');
  };

  const emitDiagnostic = (diagnostic) => {
    postToParent({
      channel: CHANNEL,
      artifactId: ARTIFACT_ID,
      sessionNonce: SESSION_NONCE,
      type: 'diagnostic',
      diagnostic,
    }, '*');
  };

  // A generated page may save through ordinary fetch('/data/file', {method:
  // 'PUT', body}). Authentication and storage identity stay in the parent.
  // Only a recent real user interaction arms writes, so merely loading an
  // Agent-authored page cannot mutate the Chat workspace.
  for (const eventName of ['pointerdown', 'keydown', 'input', 'change', 'submit']) {
    document.addEventListener(eventName, (event) => {
      if (event.isTrusted) writeArmedUntil = Date.now() + WRITE_GESTURE_WINDOW_MS;
    }, true);
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window.parent) return;
    const message = event.data;
    if (
      !message
      || message.channel !== CHANNEL
      || message.artifactId !== ARTIFACT_ID
      || message.sessionNonce !== SESSION_NONCE
      || message.type !== 'vfs.write.result'
      || typeof message.requestId !== 'string'
    ) return;
    const pending = pendingWrites.get(message.requestId);
    if (!pending) return;
    pendingWrites.delete(message.requestId);
    window.clearTimeout(pending.timer);
    pending.resolve(new Response(
      JSON.stringify(message.result || {
        error: message.error || 'The VFS write failed.',
      }),
      {
        status: Number(message.status) || (message.ok ? 200 : 500),
        headers: { 'Content-Type': 'application/json' },
      },
    ));
  });

  const errorMessage = (value) => {
    const text = value instanceof Error ? value.message : String(value || 'Unknown error');
    return text.slice(0, 500);
  };

  const displayUrl = (value) => {
    const text = String(value || '');
    for (const mount of VFS_MOUNTS) {
      if (text.startsWith(mount.root_url)) {
        return (mount.path_prefix + text.slice(mount.root_url.length)).slice(0, 500);
      }
    }
    if (text.startsWith('/')) return text.slice(0, 500);
    try {
      const parsed = new URL(text, document.baseURI);
      for (const mount of VFS_MOUNTS) {
        if (parsed.href.startsWith(mount.root_url)) {
          return (mount.path_prefix + parsed.href.slice(mount.root_url.length)).slice(0, 500);
        }
      }
      return parsed.origin === location.origin ? parsed.pathname.slice(0, 500) : parsed.origin;
    } catch {
      return text.slice(0, 500);
    }
  };

  window.addEventListener('error', (event) => {
    const target = event.target;
    if (target instanceof Element && target !== window) {
      const raw = target.getAttribute('src') || target.getAttribute('href') || target.getAttribute('poster') || '';
      emitDiagnostic({
        id: 'resource:' + displayUrl(raw),
        status: 'open',
        severity: 'error',
        kind: 'resource',
        message: 'A page resource failed to load.',
        path: displayUrl(raw),
      });
      return;
    }
    emitDiagnostic({
      id: 'script:' + (++diagnosticSequence),
      status: 'open',
      severity: 'error',
      kind: 'script',
      message: errorMessage(event.error || event.message),
      line: Number(event.lineno) || undefined,
      column: Number(event.colno) || undefined,
    });
  }, true);

  window.addEventListener('unhandledrejection', (event) => {
    emitDiagnostic({
      id: 'promise:' + (++diagnosticSequence),
      status: 'open',
      severity: 'error',
      kind: 'promise',
      message: errorMessage(event.reason),
    });
  });

  const resolveUrl = (value) => {
    if (typeof value !== 'string' || !value) return value;
    if (VFS_MOUNTS.some((mount) => value.startsWith(mount.root_url))) return value;
    let candidate = value;
    // Libraries often normalize a Linux path through the URL constructor before they
    // call fetch. Recover the reserved VFS pathname only for our own resource
    // origin; never rewrite an unrelated external URL that happens to contain
    // a reserved-looking pathname.
    const looksLikeAbsoluteUrl = /^[a-z][a-z0-9+.-]*:/i.test(candidate) || candidate.startsWith('//');
    if (!candidate.startsWith('/') && looksLikeAbsoluteUrl) {
      try {
        const parsed = new URL(candidate, document.baseURI);
        if (VFS_RESOURCE_ORIGINS.has(parsed.origin)) {
          candidate = parsed.pathname + parsed.search + parsed.hash;
        }
      } catch {
        return value;
      }
    }
    const mount = VFS_MOUNTS.find(({ path_prefix: prefix }) => candidate.startsWith(prefix));
    if (mount) {
      const suffix = candidate.slice(mount.path_prefix.length);
      return mount.root_url + (suffix.startsWith('/') ? suffix.slice(1) : suffix);
    }
    return value;
  };

  const toVirtualPath = (value) => {
    if (typeof value !== 'string' || !value) return null;
    if (value.startsWith('/')) return value.split(/[?#]/, 1)[0];
    try {
      const parsed = new URL(value, document.baseURI);
      for (const mount of VFS_MOUNTS) {
        if (parsed.href.startsWith(mount.root_url)) {
          return (
            mount.path_prefix + parsed.href.slice(mount.root_url.length)
          ).split(/[?#]/, 1)[0];
        }
      }
    } catch {
      return null;
    }
    return null;
  };

  const bodyAsText = async (body) => {
    if (body === undefined || body === null) return '';
    if (typeof body === 'string') return body;
    if (body instanceof URLSearchParams) return body.toString();
    if (body instanceof Blob) return await body.text();
    if (body instanceof FormData) {
      const fields = {};
      for (const [name, value] of body.entries()) {
        const normalized = value instanceof File
          ? { name: value.name, type: value.type, size: value.size }
          : value;
        fields[name] = Object.prototype.hasOwnProperty.call(fields, name)
          ? (Array.isArray(fields[name])
            ? [...fields[name], normalized]
            : [fields[name], normalized])
          : normalized;
      }
      return JSON.stringify(fields, null, 2);
    }
    if (body instanceof ArrayBuffer) return new TextDecoder().decode(body);
    if (ArrayBuffer.isView(body)) {
      return new TextDecoder().decode(
        new Uint8Array(body.buffer, body.byteOffset, body.byteLength),
      );
    }
    throw new TypeError(
      'Interactive VFS writes support text, JSON, URLSearchParams, FormData, Blob, and ArrayBuffer bodies.',
    );
  };

  const writeViaParent = (path, method, content, contentType) => {
    // A Save attempt is also a user checkpoint. Flush the current form state
    // before validating/writing the requested path so a failed save does not
    // discard the user's draft on a subsequent refresh.
    emit('draft', collect(), { flush: true });
    if (!path.startsWith('/data/')) {
      return Promise.resolve(new Response(
        JSON.stringify({ error: 'Interactive HTML may write only under /data/.' }),
        { status: 403, headers: { 'Content-Type': 'application/json' } },
      ));
    }
    if (!USER_ACTIVATION.isActive || Date.now() > writeArmedUntil) {
      return Promise.resolve(new Response(
        JSON.stringify({ error: 'A recent user interaction is required before saving.' }),
        { status: 403, headers: { 'Content-Type': 'application/json' } },
      ));
    }
    const requestId = 'vfs-write-' + (++writeSequence);
    return new Promise((resolve) => {
      const timer = window.setTimeout(() => {
        pendingWrites.delete(requestId);
        resolve(new Response(
          JSON.stringify({ error: 'The VFS write timed out.' }),
          { status: 504, headers: { 'Content-Type': 'application/json' } },
        ));
      }, 30_000);
      pendingWrites.set(requestId, { resolve, timer });
      postToParent({
        channel: CHANNEL,
        artifactId: ARTIFACT_ID,
        sessionNonce: SESSION_NONCE,
        type: 'vfs.write',
        requestId,
        path,
        method,
        content,
        contentType,
      }, '*');
    });
  };

  const rewriteSrcset = (value) => String(value || '').split(',').map((part) => {
    const trimmed = part.trim();
    if (!trimmed) return trimmed;
    const split = trimmed.search(/\\s/);
    const url = split < 0 ? trimmed : trimmed.slice(0, split);
    const descriptor = split < 0 ? '' : trimmed.slice(split);
    return resolveUrl(url) + descriptor;
  }).join(', ');

  const rewriteCss = (value) => String(value || '').replace(
    /url\\(\\s*(["']?)(\\/[^"')]+)\\1\\s*\\)/g,
    (_match, quote, path) => 'url(' + quote + resolveUrl(path) + quote + ')',
  );

  const rewriteElement = (element) => {
    if (!(element instanceof Element)) return;
    for (const attr of RESOURCE_ATTRS) {
      if (element.hasAttribute(attr)) {
        const current = element.getAttribute(attr);
        const next = resolveUrl(current);
        if (next !== current) nativeSetAttribute.call(element, attr, next);
      }
    }
    if (element.hasAttribute('srcset')) {
      const current = element.getAttribute('srcset') || '';
      const next = rewriteSrcset(current);
      if (next !== current) nativeSetAttribute.call(element, 'srcset', next);
    }
    if (element.hasAttribute('style')) {
      const current = element.getAttribute('style') || '';
      const next = rewriteCss(current);
      if (next !== current) nativeSetAttribute.call(element, 'style', next);
    }
    if (element.tagName === 'STYLE' && element.textContent) {
      const current = element.textContent;
      const next = rewriteCss(current);
      if (next !== current) element.textContent = next;
    }
  };

  const nativeSetAttribute = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function(name, value) {
    const lower = String(name).toLowerCase();
    if (RESOURCE_ATTRS.has(lower)) value = resolveUrl(String(value));
    else if (lower === 'srcset') value = rewriteSrcset(value);
    else if (lower === 'style') value = rewriteCss(value);
    return nativeSetAttribute.call(this, name, value);
  };

  const patchUrlProperty = (prototype, property, transform = resolveUrl) => {
    const descriptor = Object.getOwnPropertyDescriptor(prototype, property);
    if (!descriptor || !descriptor.get || !descriptor.set || descriptor.configurable === false) return;
    Object.defineProperty(prototype, property, {
      ...descriptor,
      set(value) { descriptor.set.call(this, transform(String(value))); },
    });
  };
  patchUrlProperty(HTMLImageElement.prototype, 'src');
  patchUrlProperty(HTMLImageElement.prototype, 'srcset', rewriteSrcset);
  patchUrlProperty(HTMLMediaElement.prototype, 'src');
  patchUrlProperty(HTMLSourceElement.prototype, 'src');
  patchUrlProperty(HTMLSourceElement.prototype, 'srcset', rewriteSrcset);
  patchUrlProperty(HTMLVideoElement.prototype, 'poster');
  patchUrlProperty(HTMLAnchorElement.prototype, 'href');

  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const rawUrl = typeof input === 'string'
      ? input
      : input instanceof URL
        ? input.toString()
        : input instanceof Request
          ? input.url
        : '';
    const method = String(
      init && init.method
        ? init.method
        : input instanceof Request
          ? input.method
          : 'GET',
    ).toUpperCase();
    const virtualPath = toVirtualPath(rawUrl);
    if (virtualPath && (method === 'PUT' || method === 'POST')) {
      const headers = new Headers(
        init && init.headers
          ? init.headers
          : input instanceof Request
            ? input.headers
            : undefined,
      );
      const contentType = headers.get('Content-Type') || 'text/plain';
      const contentPromise = init && Object.prototype.hasOwnProperty.call(init, 'body')
        ? bodyAsText(init.body)
        : input instanceof Request
          ? input.clone().text()
          : bodyAsText(null);
      const diagnosticId = 'vfs-write:' + (++diagnosticSequence);
      return contentPromise
        .then((content) =>
          writeViaParent(virtualPath, method, content, contentType),
        )
        .then((response) => {
          emitDiagnostic({
            id: diagnosticId,
            status: response.ok ? 'resolved' : 'open',
            severity: response.ok ? 'warning' : 'error',
            kind: 'fetch',
            message: response.ok
              ? 'Local file saved.'
              : 'The local file could not be saved (HTTP ' + response.status + ').',
            path: virtualPath,
            httpStatus: response.status,
          });
          return response;
        }, (error) => {
          emitDiagnostic({
            id: diagnosticId,
            status: 'open',
            severity: 'error',
            kind: 'fetch',
            message: errorMessage(error),
            path: virtualPath,
          });
          throw error;
        });
    }
    const path = displayUrl(rawUrl);
    const id = 'fetch:' + (++diagnosticSequence);
    const timer = window.setTimeout(() => {
      emitDiagnostic({
        id,
        status: 'open',
        severity: 'warning',
        kind: 'fetch',
        message: 'The resource request is still pending after 10 seconds.',
        path,
      });
    }, DIAGNOSTIC_TIMEOUT_MS);
    let request;
    try {
      if (typeof input === 'string') request = nativeFetch(resolveUrl(input), init);
      else if (input instanceof URL) request = nativeFetch(new URL(resolveUrl(input.toString())), init);
      else if (input instanceof Request) request = nativeFetch(new Request(resolveUrl(input.url), input), init);
      else request = nativeFetch(input, init);
    } catch (error) {
      window.clearTimeout(timer);
      emitDiagnostic({ id, status: 'open', severity: 'error', kind: 'fetch', message: errorMessage(error), path });
      throw error;
    }
    return request.then((response) => {
      window.clearTimeout(timer);
      if (response.ok) {
        emitDiagnostic({ id, status: 'resolved', severity: 'warning', kind: 'fetch', message: 'Resource loaded.', path });
      } else {
        emitDiagnostic({
          id,
          status: 'open',
          severity: 'error',
          kind: 'fetch',
          message: 'The resource request returned HTTP ' + response.status + '.',
          path,
          httpStatus: response.status,
        });
      }
      return response;
    }, (error) => {
      window.clearTimeout(timer);
      emitDiagnostic({ id, status: 'open', severity: 'error', kind: 'fetch', message: errorMessage(error), path });
      throw error;
    });
  };

  const xhrRequests = new WeakMap();
  const nativeOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    xhrRequests.set(this, { id: 'xhr:' + (++diagnosticSequence), path: displayUrl(url) });
    return nativeOpen.call(this, method, resolveUrl(String(url)), ...rest);
  };
  const nativeSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function(body) {
    const meta = xhrRequests.get(this) || { id: 'xhr:' + (++diagnosticSequence), path: '' };
    const timer = window.setTimeout(() => {
      emitDiagnostic({
        id: meta.id,
        status: 'open',
        severity: 'warning',
        kind: 'xhr',
        message: 'The resource request is still pending after 10 seconds.',
        path: meta.path,
      });
    }, DIAGNOSTIC_TIMEOUT_MS);
    this.addEventListener('loadend', () => {
      window.clearTimeout(timer);
      if (this.status >= 200 && this.status < 400) {
        emitDiagnostic({ id: meta.id, status: 'resolved', severity: 'warning', kind: 'xhr', message: 'Resource loaded.', path: meta.path });
      } else {
        emitDiagnostic({
          id: meta.id,
          status: 'open',
          severity: 'error',
          kind: 'xhr',
          message: this.status ? 'The resource request returned HTTP ' + this.status + '.' : 'The resource request failed.',
          path: meta.path,
          httpStatus: this.status || undefined,
        });
      }
    }, { once: true });
    return nativeSend.call(this, body);
  };

  // CSS authored by a dynamic script bypasses HTML attribute rewriting.
  // Route the standard mutation APIs through the same resolver as fetch and
  // DOM URL attributes; MutationObserver below remains the innerHTML fallback.
  const nativeSetProperty = CSSStyleDeclaration.prototype.setProperty;
  CSSStyleDeclaration.prototype.setProperty = function(property, value, priority) {
    return nativeSetProperty.call(this, property, rewriteCss(value), priority);
  };
  patchUrlProperty(CSSStyleDeclaration.prototype, 'cssText', rewriteCss);
  for (const property of [
    'background', 'backgroundImage', 'borderImage', 'content', 'cursor',
    'listStyle', 'listStyleImage', 'mask', 'maskImage',
  ]) {
    patchUrlProperty(CSSStyleDeclaration.prototype, property, rewriteCss);
  }
  const nativeInsertRule = CSSStyleSheet.prototype.insertRule;
  CSSStyleSheet.prototype.insertRule = function(rule, index) {
    return nativeInsertRule.call(this, rewriteCss(rule), index);
  };
  if (CSSStyleSheet.prototype.replaceSync) {
    const nativeReplaceSync = CSSStyleSheet.prototype.replaceSync;
    CSSStyleSheet.prototype.replaceSync = function(text) {
      return nativeReplaceSync.call(this, rewriteCss(text));
    };
  }
  if (CSSStyleSheet.prototype.replace) {
    const nativeReplace = CSSStyleSheet.prototype.replace;
    CSSStyleSheet.prototype.replace = function(text) {
      return nativeReplace.call(this, rewriteCss(text));
    };
  }

  const controls = () => Array.from(document.querySelectorAll('input[name], select[name], textarea[name]'));

  const collect = () => {
    const fields = {};
    for (const control of controls()) {
      if (control.disabled) continue;
      const name = control.name;
      let value;
      if (control instanceof HTMLInputElement && control.type === 'radio') {
        if (!control.checked) continue;
        value = control.value;
      } else if (control instanceof HTMLInputElement && control.type === 'checkbox') {
        value = control.checked;
      } else if (control instanceof HTMLSelectElement && control.multiple) {
        value = Array.from(control.selectedOptions).map((option) => option.value);
      } else {
        value = control.value;
      }
      if (Object.prototype.hasOwnProperty.call(fields, name)) {
        fields[name] = Array.isArray(fields[name])
          ? [...fields[name], value]
          : [fields[name], value];
      } else {
        fields[name] = value;
      }
    }
    return { schema_version: 1, fields };
  };

  const restoreControl = (control) => {
    const fields = INITIAL_STATE && typeof INITIAL_STATE.fields === 'object'
      ? INITIAL_STATE.fields
      : {};
    if (!control.name || !Object.prototype.hasOwnProperty.call(fields, control.name)) return;
    const value = fields[control.name];
    if (control instanceof HTMLInputElement && control.type === 'radio') {
      control.checked = Array.isArray(value) ? value.includes(control.value) : String(value) === control.value;
    } else if (control instanceof HTMLInputElement && control.type === 'checkbox') {
      control.checked = Array.isArray(value) ? value.includes(control.value) : Boolean(value);
    } else if (control instanceof HTMLSelectElement && control.multiple) {
      const selected = new Set(Array.isArray(value) ? value.map(String) : [String(value)]);
      for (const option of control.options) option.selected = selected.has(option.value);
    } else if (value !== undefined && value !== null) {
      control.value = Array.isArray(value) ? String(value[0] ?? '') : String(value);
    }
  };

  const prepareTree = (root) => {
    if (root instanceof Element) rewriteElement(root);
    if (root.querySelectorAll) {
      root.querySelectorAll('*').forEach(rewriteElement);
      root.querySelectorAll('input[name], select[name], textarea[name]').forEach((control) => {
        restoreControl(control);
        if (FROZEN) control.disabled = true;
      });
      if (FROZEN) root.querySelectorAll('button').forEach((button) => { button.disabled = true; });
    }
  };

  // HTML parsers may schedule image/media fetches before MutationObserver runs.
  // Parse dynamic markup inside an inert template, apply the same resolver used
  // by fetch/DOM properties, and only then attach it to the live document.
  const innerHtmlDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
  const rewriteHtmlFragment = (value) => {
    if (!innerHtmlDescriptor || !innerHtmlDescriptor.get || !innerHtmlDescriptor.set) {
      return String(value);
    }
    const template = document.createElement('template');
    innerHtmlDescriptor.set.call(template, String(value));
    prepareTree(template.content);
    return innerHtmlDescriptor.get.call(template);
  };
  if (innerHtmlDescriptor && innerHtmlDescriptor.get && innerHtmlDescriptor.set && innerHtmlDescriptor.configurable) {
    Object.defineProperty(Element.prototype, 'innerHTML', {
      ...innerHtmlDescriptor,
      set(value) {
        innerHtmlDescriptor.set.call(this, rewriteHtmlFragment(value));
      },
    });
  }
  const nativeInsertAdjacentHtml = Element.prototype.insertAdjacentHTML;
  Element.prototype.insertAdjacentHTML = function(position, value) {
    return nativeInsertAdjacentHtml.call(this, position, rewriteHtmlFragment(value));
  };

  const observer = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type === 'attributes') rewriteElement(mutation.target);
      if (mutation.type === 'childList' && mutation.target instanceof HTMLStyleElement) {
        rewriteElement(mutation.target);
      }
      mutation.addedNodes.forEach(prepareTree);
    }
  });
  observer.observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['src', 'srcset', 'poster', 'href', 'style'],
  });

  document.addEventListener('input', () => { if (!FROZEN) emit('draft', collect()); });
  document.addEventListener('change', () => { if (!FROZEN) emit('draft', collect()); });
  document.addEventListener('submit', (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!FROZEN && event.isTrusted && navigator.userActivation.isActive) {
      const form = event.target instanceof HTMLFormElement ? event.target : null;
      const action = form ? String(form.getAttribute('action') || '') : '';
      const path = toVirtualPath(action);
      if (!path || !path.startsWith('/data/')) {
        emitDiagnostic({
          id: 'form-save:' + (++diagnosticSequence),
          status: 'open',
          severity: 'error',
          kind: 'contract',
          message: 'A save form must use an action under /data/.',
          path: displayUrl(action),
        });
        return;
      }
      const diagnosticId = 'form-save:' + (++diagnosticSequence);
      void writeViaParent(
        path,
        'POST',
        JSON.stringify(collect(), null, 2),
        'application/json',
      ).then((response) => {
        emitDiagnostic({
          id: diagnosticId,
          status: response.ok ? 'resolved' : 'open',
          severity: response.ok ? 'warning' : 'error',
          kind: 'fetch',
          message: response.ok
            ? 'Local file saved.'
            : 'The local file could not be saved (HTTP ' + response.status + ').',
          path,
          httpStatus: response.status,
        });
      });
    }
  }, true);
  document.addEventListener('click', (event) => {
    if (!event.isTrusted || event.defaultPrevented) return;
    const target = event.target instanceof Element
      ? event.target.closest('a[href]')
      : null;
    if (!(target instanceof HTMLAnchorElement)) return;
    const path = toVirtualPath(target.href);
    if (!path) return;
    event.preventDefault();
    emit('preview.open', undefined, { path });
  }, true);
  document.addEventListener('DOMContentLoaded', () => {
    prepareTree(document);
    emit('ready', collect());
  });
  // Establish the trusted bridge nonce before the parser can execute any
  // Agent-authored script that follows this injected bootstrap. postMessage
  // tasks from one Window are delivered in send order, so a forged ready event
  // cannot become the parent's first accepted nonce.
  emit('ready');
})();
</script>`;

  if (/<head(?:\s[^>]*)?>/i.test(html)) {
    return html.replace(/<head(?:\s[^>]*)?>/i, (head) => `${head}${bootstrap}`);
  }
  if (/<html(?:\s[^>]*)?>/i.test(html)) {
    return html.replace(/<html(?:\s[^>]*)?>/i, (tag) => `${tag}<head>${bootstrap}</head>`);
  }
  return `<!doctype html><html><head>${bootstrap}</head><body>${html}</body></html>`;
}

/**
 * Return the exact invariant inline runtime accepted by the application CSP.
 * Dynamic artifact capabilities live only in the script element's escaped
 * data attribute, so the executable text has one build-time SHA-256 value.
 */
export function interactiveBootstrapScriptSource(): string {
  const document = buildInteractiveHtmlDocument({
    artifactId: 'csp-hash-source',
    html: '<!doctype html><html><head></head><body></body></html>',
    resourceMounts: [],
    baseUrl: 'http://localhost/',
    initialState: {},
    frozen: false,
  });
  const match = document.match(
    /<script data-vibecanvas-interactive-runtime[^>]*>([\s\S]*?)<\/script>/,
  );
  if (!match?.[1]) throw new Error('interactive bootstrap script source is missing');
  return match[1];
}
