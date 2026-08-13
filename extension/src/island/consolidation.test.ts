import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const extensionRoot = resolve(process.cwd());

describe('browser-control surface consolidation', () => {
  it('injects the Dynamic Island as the only control surface', () => {
    const manifest = JSON.parse(readFileSync(resolve(extensionRoot, 'manifest.json'), 'utf8')) as {
      content_scripts: Array<{ js: string[] }>;
    };
    const scripts = manifest.content_scripts.flatMap((entry) => entry.js);
    expect(scripts).toEqual(['island/content.js']);
    expect(scripts).not.toContain('content-overlay.js');
  });

  it('keeps the Island bundle self-contained and exposes separate page feedback', () => {
    const source = readFileSync(resolve(extensionRoot, 'src/island/content.ts'), 'utf8');
    expect(source).not.toMatch(/^import\s/m);
    expect(source).toContain('PAGE_HIGHLIGHT');
    expect(source).toContain('pointer-events: none');
    expect(source).toContain('prefers-reduced-motion');
    // Resetting every property on a visible shadow host can force pathological
    // style/compositor invalidation on large transformed canvases.
    expect(source).not.toContain(':host { all: initial;');
    expect(source).toContain(':host { color-scheme: dark; }');
  });
});
