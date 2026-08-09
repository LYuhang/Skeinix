/**
 * Unit tests for `filterWorkflows` — the workspace list search rule.
 *
 * Covers the contract the page relies on: case-insensitive substring match
 * against BOTH name and id, empty query → all, no match → empty.
 */
import { describe, expect, it } from 'vitest';
import { filterWorkflows } from '@/pages/workspace/filterWorkflows';

const items = [
  { wf_id: 'wf_abc123', workflow_name: 'Alpha Pipeline' },
  { wf_id: 'wf_def456', workflow_name: 'Beta Report' },
  { wf_id: 'wf_ghi789', workflow_name: '' },
];

describe('filterWorkflows', () => {
  it('returns all items for an empty query', () => {
    expect(filterWorkflows(items, '')).toEqual(items);
    expect(filterWorkflows(items, '   ')).toEqual(items);
  });

  it('matches by name (case-insensitive)', () => {
    const res = filterWorkflows(items, 'alpha');
    expect(res).toHaveLength(1);
    expect(res[0].wf_id).toBe('wf_abc123');
  });

  it('matches by name with different casing', () => {
    expect(filterWorkflows(items, 'BETA')).toHaveLength(1);
  });

  it('matches by id (case-insensitive)', () => {
    const res = filterWorkflows(items, 'DEF456');
    expect(res).toHaveLength(1);
    expect(res[0].wf_id).toBe('wf_def456');
  });

  it('matches an id even when the name is empty', () => {
    const res = filterWorkflows(items, 'ghi');
    expect(res).toHaveLength(1);
    expect(res[0].wf_id).toBe('wf_ghi789');
  });

  it('matches the common wf_ prefix across all items', () => {
    expect(filterWorkflows(items, 'wf_')).toHaveLength(3);
  });

  it('returns an empty array when nothing matches', () => {
    expect(filterWorkflows(items, 'zzz-nope')).toEqual([]);
  });
});
