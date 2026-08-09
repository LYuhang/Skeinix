import { afterEach, describe, expect, it } from 'vitest';

import {
  basePathFromModuleUrl,
  getApiBase,
  getBasePath,
  normalizeBasePath,
  resolveApiUrl,
} from '@/lib/base-path';

afterEach(() => {
  delete window.__VIBECANVAS_RUNTIME_CONFIG__;
});

describe('deployment base-path resolution', () => {
  it('infers arbitrary proxy prefixes from emitted module URLs', () => {
    expect(
      basePathFromModuleUrl(
        'https://example.test/random/session/prefix/assets/index-abc123.js',
      ),
    ).toBe('/random/session/prefix');
    expect(basePathFromModuleUrl('https://example.test/assets/index-abc123.js')).toBe('');
  });

  it('normalizes configured root, path, and URL values', () => {
    expect(normalizeBasePath('/')).toBe('');
    expect(normalizeBasePath('studio/nested/')).toBe('/studio/nested');
    expect(normalizeBasePath('https://example.test/studio/')).toBe('/studio');
  });

  it('prefers host-injected runtime coordinates without hard-coded route names', () => {
    window.__VIBECANVAS_RUNTIME_CONFIG__ = {
      basePath: '/runtime/mount/',
      apiBase: 'https://api.example.test/v2/',
    };

    expect(getBasePath()).toBe('/runtime/mount');
    expect(getApiBase()).toBe('https://api.example.test/v2');
    expect(resolveApiUrl('/api/v1/vfs/resources/token/data/items.jsonl')).toBe(
      'https://api.example.test/v2/api/v1/vfs/resources/token/data/items.jsonl',
    );
  });

  it('preserves an opaque same-origin proxy prefix for backend resource URLs', () => {
    window.__VIBECANVAS_RUNTIME_CONFIG__ = {
      basePath: '/pws/session/tasks',
      apiBase: '/pws/session/tasks',
    };

    expect(resolveApiUrl('/api/v1/vfs/resources/token/mount/data.jsonl')).toBe(
      `${window.location.origin}/pws/session/tasks/api/v1/vfs/resources/token/mount/data.jsonl`,
    );
  });
});
