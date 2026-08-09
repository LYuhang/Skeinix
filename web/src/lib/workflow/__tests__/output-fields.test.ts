import { describe, expect, it } from 'vitest';

import { outputFieldCandidates } from '@/lib/workflow/output-fields';

describe('outputFieldCandidates', () => {
  it('lists every node\'s output fields across the draft', () => {
    const draft = {
      node_1: {
        node_name: 'start',
        node_type: 'StartNode',
        output_fields: { a: { type: 'string' }, b: { type: 'integer' } },
      },
      node_2: {
        node_name: 'code',
        node_type: 'CodeNode',
        output_fields: { result: { type: 'string' } },
      },
      __meta__: { active_v: 1 },
    };
    expect(outputFieldCandidates(draft)).toEqual([
      { node: 'start', field: 'a', label: 'start.a' },
      { node: 'start', field: 'b', label: 'start.b' },
      { node: 'code', field: 'result', label: 'code.result' },
    ]);
  });

  it('skips the reserved __meta__ (and other __*) keys', () => {
    const draft = {
      __meta__: { output_fields: { nope: {} } },
      __whatever__: { output_fields: { nope2: {} } },
      node_1: { node_name: 'n', output_fields: { x: {} } },
    };
    expect(outputFieldCandidates(draft)).toEqual([
      { node: 'n', field: 'x', label: 'n.x' },
    ]);
  });

  it('handles nodes without output_fields (and non-object entries)', () => {
    const draft = {
      node_1: { node_name: 'n1' }, // no output_fields
      node_2: { node_name: 'n2', output_fields: {} }, // empty
      node_3: null, // non-object
      node_4: { node_name: 'n4', output_fields: { y: {} } },
    };
    expect(outputFieldCandidates(draft)).toEqual([
      { node: 'n4', field: 'y', label: 'n4.y' },
    ]);
  });

  it('falls back to the node id when node_name is missing/blank', () => {
    const draft = {
      node_1: { output_fields: { z: {} } },
      node_2: { node_name: '', output_fields: { w: {} } },
    };
    expect(outputFieldCandidates(draft)).toEqual([
      { node: 'node_1', field: 'z', label: 'node_1.z' },
      { node: 'node_2', field: 'w', label: 'node_2.w' },
    ]);
  });

  it('returns [] for null/undefined drafts', () => {
    expect(outputFieldCandidates(null)).toEqual([]);
    expect(outputFieldCandidates(undefined)).toEqual([]);
  });
});
