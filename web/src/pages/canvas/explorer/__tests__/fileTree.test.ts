import { describe, it, expect } from 'vitest';
import { buildFileTree } from '@/pages/canvas/explorer/fileTree';
import type { VfsEntry } from '@/lib/api/vfs';

const E = (path: string): VfsEntry => ({
  path, kind: 'artifact', content_type: 'table/jsonl', abstract: '',
  size_bytes: 1, wf_version: null, last_access: 0, stale: false,
  capabilities: ['read', 'download', 'copy_path'],
});

describe('buildFileTree', () => {
  it('always yields the canonical top folders in taxonomy order, even when empty', () => {
    const tree = buildFileTree([]);
    expect(tree.map((n) => n.name)).toEqual(['mount', 'data', 'memory', 'logs']);
    expect(tree.every((n) => n.kind === 'folder' && n.children.length === 0)).toBe(true);
  });

  it('renders the /logs canonical folder', () => {
    const tree = buildFileTree([E('/logs/run_1.txt')]);
    const logs = tree.find((n) => n.name === 'logs')!;
    expect(logs.kind).toBe('folder');
    expect(logs.children[0]).toMatchObject({ name: 'run_1.txt', kind: 'file', path: '/logs/run_1.txt' });
  });

  it('lazy-creates a legacy top folder (/files, /exec) AFTER the canonical ones — never drops it', () => {
    const tree = buildFileTree([E('/files/old.txt'), E('/exec/log.txt')]);
    const names = tree.map((n) => n.name);
    // Canonical folders come first, in order; legacy ones appended after.
    expect(names.slice(0, 4)).toEqual(['mount', 'data', 'memory', 'logs']);
    expect(names).toContain('files');
    expect(names).toContain('exec');
    const files = tree.find((n) => n.name === 'files')!;
    expect(files.children[0]).toMatchObject({ name: 'old.txt', kind: 'file', path: '/files/old.txt' });
  });

  it('places a /mount upload under the mount folder', () => {
    const tree = buildFileTree([E('/mount/x.csv')]);
    const mount = tree.find((n) => n.name === 'mount')!;
    expect(mount.children).toHaveLength(1);
    expect(mount.children[0]).toMatchObject({ name: 'x.csv', kind: 'file', path: '/mount/x.csv' });
  });

  it('places a flat file under its top folder', () => {
    const tree = buildFileTree([E('/data/cells_1.jsonl')]);
    const data = tree.find((n) => n.name === 'data')!;
    expect(data.children).toHaveLength(1);
    expect(data.children[0]).toMatchObject({ name: 'cells_1.jsonl', kind: 'file', path: '/data/cells_1.jsonl' });
  });

  it('nests a multi-level path into subfolders', () => {
    const tree = buildFileTree([E('/data/batch1/rows_1.jsonl')]);
    const data = tree.find((n) => n.name === 'data')!;
    const batch = data.children.find((c) => c.name === 'batch1')!;
    expect(batch.kind).toBe('folder');
    expect(batch.children[0]).toMatchObject({ name: 'rows_1.jsonl', kind: 'file' });
  });

  it('shows the empty folder a .vibekeep sentinel represents, but hides the sentinel file', () => {
    const tree = buildFileTree([E('/data/foo/.vibekeep')]);
    const data = tree.find((n) => n.name === 'data')!;
    const foo = data.children.find((c) => c.name === 'foo')!;
    expect(foo.kind).toBe('folder');
    expect(foo.path).toBe('/data/foo');
    expect(foo.children).toHaveLength(0); // sentinel NOT a visible file leaf
  });

  it('sorts folders before files, alphabetically', () => {
    const tree = buildFileTree([E('/data/z.jsonl'), E('/data/a/x.jsonl'), E('/data/a.jsonl')]);
    const data = tree.find((n) => n.name === 'data')!;
    expect(data.children.map((c) => `${c.kind}:${c.name}`)).toEqual(['folder:a', 'file:a.jsonl', 'file:z.jsonl']);
  });
});
