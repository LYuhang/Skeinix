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
    ['/data/report.pdf', null, 'pdf', 'PDF'],
    ['/data/report.docx', null, 'document', 'Document'],
    ['/data/slides.pptx', null, 'presentation', 'Presentation'],
    ['/data/source.ts', null, 'code', 'TS'],
    ['/data/archive.zip', null, 'archive', 'Archive'],
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

  it.each([
    ['slides/deck.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'presentation'],
    ['docs/report.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'document'],
  ])('does not mistake Open XML Office containers for XML source', (path, mime, kind) => {
    const result = resolveFileCapability(path, mime);
    expect(result.kind).toBe(kind);
    expect(result.source).toBe(false);
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
