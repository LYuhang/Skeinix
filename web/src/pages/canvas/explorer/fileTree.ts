import type { VfsEntry, VfsRunEntry } from '@/lib/api/vfs';

export interface FileTreeNode {
  name: string;
  path: string; // folder: '/data' or '/data/batch1'; file: the full entry path
  kind: 'folder' | 'file';
  children: FileTreeNode[];
  entry?: VfsEntry; // only on files
}

export interface RunFileTreeNode {
  name: string;
  path: string;
  kind: 'folder' | 'file';
  children: RunFileTreeNode[];
  entry?: VfsRunEntry; // only on files
}

/**
 * Run-tier (WORKFLOW_SANDBOX) tree. Unlike `buildFileTree`, the top folders
 * are NOT the fixed data/files/exec/memory set — a run writes whatever paths
 * it likes, so folders are inferred from the entry paths themselves.
 */
export function buildRunFileTree(entries: VfsRunEntry[]): RunFileTreeNode[] {
  const root: RunFileTreeNode = { name: '', path: '/', kind: 'folder', children: [] };
  for (const e of entries) {
    const segs = e.path.split('/').filter(Boolean);
    if (segs.length === 0) continue;
    let cur = root;
    for (let i = 0; i < segs.length - 1; i++) {
      const folderPath = '/' + segs.slice(0, i + 1).join('/');
      let next = cur.children.find((c) => c.kind === 'folder' && c.path === folderPath);
      if (!next) {
        next = { name: segs[i], path: folderPath, kind: 'folder', children: [] };
        cur.children.push(next);
      }
      cur = next;
    }
    cur.children.push({
      name: segs[segs.length - 1],
      path: e.path,
      kind: 'file',
      children: [],
      entry: e,
    });
  }
  const sortRec = (node: RunFileTreeNode) => {
    node.children.sort((a, b) =>
      a.kind !== b.kind ? (a.kind === 'folder' ? -1 : 1) : a.name.localeCompare(b.name),
    );
    node.children.forEach(sortRec);
  };
  sortRec(root);
  return root.children;
}

// Canonical top folders, always shown in this order. `/mount` is user-level
// shared storage; `/data`, `/memory`, and `/logs` belong to a Chat workspace.
const CANONICAL = ['mount', 'data', 'memory', 'logs'] as const;

// Auto-managed 0-byte sentinel that persists an EMPTY directory across the VFS
// write-back → re-materialize boundary (mirrors backend DIR_KEEP_SENTINEL). Its
// PARENT folder must still appear in the tree, but the sentinel itself is hidden
// — so we create the intermediate folders for its path but never add it as a
// file leaf.
const DIR_KEEP_SENTINEL = '.vibekeep';

export function buildFileTree(entries: VfsEntry[]): FileTreeNode[] {
  const roots: Record<string, FileTreeNode> = {};
  // Canonical roots first, in declared order.
  const order: string[] = [...CANONICAL];
  for (const top of CANONICAL) {
    roots[top] = { name: top, path: `/${top}`, kind: 'folder', children: [] };
  }
  for (const e of entries) {
    const segs = e.path.split('/').filter(Boolean); // ['data','batch1','x.jsonl']
    if (segs.length < 2) continue; // need at least top + leaf
    let root = roots[segs[0]];
    if (!root) {
      // Unknown top segment (e.g. legacy `/files`, `/exec`, or any future
      // prefix the backend writes before its own rename). LAZY-CREATE a root
      // and append it AFTER the canonical ones — never drop the entry.
      root = { name: segs[0], path: `/${segs[0]}`, kind: 'folder', children: [] };
      roots[segs[0]] = root;
      order.push(segs[0]);
    }
    // The hidden empty-dir sentinel: create EVERY intermediate folder (incl. the
    // dir that holds it) so the otherwise-empty folder shows, but never add the
    // `.vibekeep` itself as a file leaf.
    const isKeep = segs[segs.length - 1] === DIR_KEEP_SENTINEL;
    let cur = root;
    for (let i = 1; i < segs.length - 1; i++) {
      const folderPath = '/' + segs.slice(0, i + 1).join('/');
      let next = cur.children.find((c) => c.kind === 'folder' && c.path === folderPath);
      if (!next) {
        next = { name: segs[i], path: folderPath, kind: 'folder', children: [] };
        cur.children.push(next);
      }
      cur = next;
    }
    if (isKeep) continue; // folder(s) ensured above; sentinel stays hidden
    cur.children.push({ name: segs[segs.length - 1], path: e.path, kind: 'file', children: [], entry: e });
  }
  const sortRec = (node: FileTreeNode) => {
    node.children.sort((a, b) =>
      a.kind !== b.kind ? (a.kind === 'folder' ? -1 : 1) : a.name.localeCompare(b.name),
    );
    node.children.forEach(sortRec);
  };
  const result = order.map((t) => roots[t]);
  result.forEach(sortRec);
  return result;
}
