import { describe, expect, it } from 'vitest';

import { resolveCodePreviewLanguage } from '../code-preview-language';

describe('resolveCodePreviewLanguage', () => {
  it('selects Python syntax support from either extension or MIME metadata', () => {
    expect(resolveCodePreviewLanguage({ name: 'worker.py', contentType: 'text/plain' })).toBe('python');
    expect(resolveCodePreviewLanguage({ name: 'worker', contentType: 'text/x-python' })).toBe('python');
  });

  it('uses the code editor for other supported source files', () => {
    expect(resolveCodePreviewLanguage({ name: 'runtime.ts', contentType: 'text/plain' })).toBe('plain');
    expect(resolveCodePreviewLanguage({ name: 'settings', contentType: 'application/json' })).toBe('plain');
    expect(resolveCodePreviewLanguage({ name: 'Dockerfile', contentType: 'text/plain' })).toBe('plain');
  });

  it('keeps prose and logs on the lightweight text surface', () => {
    expect(resolveCodePreviewLanguage({ name: 'notes.txt', contentType: 'text/plain' })).toBeNull();
    expect(resolveCodePreviewLanguage({ name: 'service.log', contentType: 'text/plain' })).toBeNull();
  });
});
