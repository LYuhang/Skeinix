import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  fetchPreviewRendition,
  resolvePreviewResourceUrl,
} from '@/lib/api/previews';
import type { PreviewResourceSessionV1 } from '@/lib/preview/protocol';

vi.mock('@/stores/auth', () => ({
  useAuthStore: {
    getState: () => ({ token: 'preview-token', handle401: vi.fn() }),
  },
}));

afterEach(() => vi.restoreAllMocks());

const session: PreviewResourceSessionV1 = {
  schemaVersion: 1,
  resourceMounts: [
    { pathPrefix: '/', rootUrl: 'https://api.test/resources/workspace/' },
    { pathPrefix: '/mount/', rootUrl: 'https://api.test/resources/mount/' },
  ],
  baseUrl: 'https://api.test/resources/workspace/data/diagrams/',
  expiresIn: 3600,
};

describe('resolvePreviewResourceUrl', () => {
  it('uses the most specific capability mount', () => {
    expect(resolvePreviewResourceUrl('/data/images/chart.png', session)).toBe(
      'https://api.test/resources/workspace/data/images/chart.png',
    );
    expect(resolvePreviewResourceUrl('/mount/brand/logo.svg', session)).toBe(
      'https://api.test/resources/mount/brand/logo.svg',
    );
  });

  it('rejects external, relative, and traversal references', () => {
    expect(resolvePreviewResourceUrl('https://example.com/tracker.png', session)).toBeNull();
    expect(resolvePreviewResourceUrl('images/chart.png', session)).toBeNull();
    expect(resolvePreviewResourceUrl('/data/../memory/private.png', session)).toBeNull();
    expect(resolvePreviewResourceUrl('/data\\private.png', session)).toBeNull();
  });
});

describe('fetchPreviewRendition', () => {
  it('loads a private Office rendition with the current Bearer session', async () => {
    const payload = new Uint8Array([0x25, 0x50, 0x44, 0x46]);
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(payload, {
        status: 200,
        headers: { 'Content-Type': 'application/pdf' },
      }),
    );

    const result = await fetchPreviewRendition(
      '/api/v1/previews/office-rendition?scope=chat&path=%2Fdata%2Fbrief.docx',
    );

    expect(new Uint8Array(result)).toEqual(payload);
    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain('/api/v1/previews/office-rendition');
    expect(new Headers((init as RequestInit).headers).get('Authorization')).toBe(
      'Bearer preview-token',
    );
  });
});
