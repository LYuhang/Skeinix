import { describe, expect, it } from 'vitest';

import type { PreviewResourceSessionV1 } from '@/lib/preview/protocol';
import { resolveMarkdownImageUrl } from '../markdown-preview-resource';

const session: PreviewResourceSessionV1 = {
  schemaVersion: 1,
  resourceMounts: [{
    pathPrefix: '/',
    rootUrl: 'https://api.test/resources/token/',
  }],
  baseUrl: 'https://api.test/resources/token/data/handbooks/',
  expiresIn: 3600,
};

describe('resolveMarkdownImageUrl', () => {
  it('resolves relative and absolute VFS image references', () => {
    expect(resolveMarkdownImageUrl('architecture.svg', session)).toBe(
      'https://api.test/resources/token/data/handbooks/architecture.svg',
    );
    expect(resolveMarkdownImageUrl('../brand/logo.svg', session)).toBe(
      'https://api.test/resources/token/data/brand/logo.svg',
    );
    expect(resolveMarkdownImageUrl('/data/brand/logo.svg', session)).toBe(
      'https://api.test/resources/token/data/brand/logo.svg',
    );
  });

  it('preserves ordinary remote images and rejects executable schemes', () => {
    expect(resolveMarkdownImageUrl('https://example.com/chart.png', session)).toBe(
      'https://example.com/chart.png',
    );
    expect(resolveMarkdownImageUrl('javascript:alert(1)', session)).toBeNull();
    expect(resolveMarkdownImageUrl('//example.com/tracker.png', session)).toBeNull();
  });
});
