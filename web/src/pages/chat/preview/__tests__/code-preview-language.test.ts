import { describe, expect, it } from 'vitest';

import { resolveCodePreviewLanguage } from '../code-preview-language';

describe('resolveCodePreviewLanguage', () => {
  it('selects Python syntax support from either extension or MIME metadata', () => {
    expect(resolveCodePreviewLanguage({ name: 'worker.py', contentType: 'text/plain' })?.id).toBe('Python');
    expect(resolveCodePreviewLanguage({ name: 'worker', contentType: 'text/x-python' })?.id).toBe('Python');
  });

  it('selects syntax packages for common source files', () => {
    expect(resolveCodePreviewLanguage({ name: 'runtime.ts', contentType: 'text/plain' })?.id).toBe('TypeScript');
    expect(resolveCodePreviewLanguage({ name: 'settings', contentType: 'application/json' })?.id).toBe('JSON');
    expect(resolveCodePreviewLanguage({ name: 'Dockerfile', contentType: 'text/plain' })?.id).toBe('Dockerfile');
    expect(resolveCodePreviewLanguage({ name: 'main.go', contentType: 'text/plain' })?.id).toBe('Go');
  });

  it('keeps prose and logs on the lightweight text surface', () => {
    expect(resolveCodePreviewLanguage({ name: 'notes.txt', contentType: 'text/plain' })).toBeNull();
    expect(resolveCodePreviewLanguage({ name: 'service.log', contentType: 'text/plain' })).toBeNull();
  });
});
