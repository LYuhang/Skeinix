import { describe, expect, it } from 'vitest';

import { isTextFileCapability, resolveFileCapability } from '@/lib/files/capabilities';

describe('shared file capability registry', () => {
  it.each([
    ['/data/a.jsonl', 'text/plain', 'jsonl', 'JSONL'],
    ['/data/a.csv', 'text/csv', 'delimited', 'CSV'],
    ['/data/a.md', null, 'markdown', 'Markdown'],
    ['/data/a.py', 'text/plain', 'python', 'Python'],
    ['/data/a.png', 'application/octet-stream', 'image', 'Image'],
    ['/data/a.wav', 'application/octet-stream', 'audio', 'Audio'],
    ['/data/a.xlsx', null, 'workbook', 'Excel'],
  ])('resolves %s consistently', (path, mime, kind, label) => {
    const result = resolveFileCapability(path, mime);
    expect(result.kind).toBe(kind);
    expect(result.label).toBe(label);
  });

  it('does not guess unknown application payloads into text or JSON', () => {
    const result = resolveFileCapability('/data/blob.custom', 'application/x-custom');
    expect(result.kind).toBe('unknown');
    expect(result.preview).toBe(false);
    expect(isTextFileCapability(result)).toBe(false);
  });

  it('marks HTML as source plus a sandbox-required rendered preview', () => {
    const result = resolveFileCapability('/data/report.html', 'text/html; charset=utf-8');
    expect(result).toMatchObject({
      kind: 'html',
      source: true,
      safeRenderedPreview: true,
      editable: true,
    });
  });
});
