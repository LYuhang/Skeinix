/**
 * Stream 8 M4 — `nodeWarnings(draft)` pure selector. Asserts each cheap LOCAL
 * check fires for its broken fixture and a clean node has NO entry (absence
 * from the map is the no-warning signal). These mirror the same rules the
 * route Check now enforces (`condition.py` / `parallel.py` / `loop.py` /
 * `workflow.py` reference rule) but only the by-construction-local subset.
 */
import { describe, expect, it } from 'vitest';
import { nodeWarnings } from '@/pages/canvas/nodeWarnings';

describe('nodeWarnings', () => {
  it('null draft → empty map', () => {
    expect(nodeWarnings(null).size).toBe(0);
  });

  it('ignores __meta__ and other __-prefixed keys', () => {
    const w = nodeWarnings({
      __meta__: { foo: 1 },
      node_1: { node_type: 'StartNode', children: [] },
    });
    expect(w.has('__meta__')).toBe(false);
  });

  it('flags ConditionNode with an empty non-others condition_str', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'route',
        node_type: 'ConditionNode',
        children: ['node_2'],
        node_config: {
          conditions: [
            { condition_name: 'hi', condition_str: '', next_node_id: 'node_2' },
            { condition_name: 'others', condition_str: 'others', next_node_id: null },
          ],
        },
      },
      node_2: { node_name: 'b', node_type: 'CodeNode', children: [] },
    });
    expect(w.get('node_1')).toContain('canvas.warn.conditionEmptyExpr');
  });

  it('flags ConditionNode missing the others fallback', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'route',
        node_type: 'ConditionNode',
        children: ['node_2'],
        node_config: {
          conditions: [
            { condition_name: 'hi', condition_str: '{x}>1', next_node_id: 'node_2' },
          ],
        },
      },
      node_2: { node_name: 'b', node_type: 'CodeNode', children: [] },
    });
    expect(w.get('node_1')).toContain('canvas.warn.conditionNoOthers');
  });

  it('flags ConditionNode when conditions targets ≠ children', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'route',
        node_type: 'ConditionNode',
        // children says node_3, conditions only map node_2 → mismatch.
        children: ['node_3'],
        node_config: {
          conditions: [
            { condition_name: 'hi', condition_str: '{x}>1', next_node_id: 'node_2' },
            { condition_name: 'others', condition_str: 'others', next_node_id: null },
          ],
        },
      },
      node_2: { node_name: 'b', node_type: 'CodeNode', children: [] },
      node_3: { node_name: 'c', node_type: 'CodeNode', children: [] },
    });
    expect(w.get('node_1')).toContain('canvas.warn.conditionMismatch');
  });

  it('clean ConditionNode (matched targets + others + filled exprs) → no entry', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'route',
        node_type: 'ConditionNode',
        children: ['node_2'],
        node_config: {
          conditions: [
            { condition_name: 'hi', condition_str: '{x}>1', next_node_id: 'node_2' },
            { condition_name: 'others', condition_str: 'others', next_node_id: null },
          ],
        },
      },
      node_2: { node_name: 'b', node_type: 'CodeNode', children: [] },
    });
    expect(w.has('node_1')).toBe(false);
  });

  it('flags an unpaired ParallelStartNode (null parallel_end_node_id)', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'p',
        node_type: 'ParallelStartNode',
        children: [],
        node_config: { branches: {}, parallel_end_node_id: null },
      },
    });
    expect(w.get('node_1')).toContain('canvas.warn.unpairedParallel');
  });

  it('flags an unpaired ParallelEndNode (missing back-pointer)', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'pe',
        node_type: 'ParallelEndNode',
        children: [],
        node_config: {},
      },
    });
    expect(w.get('node_1')).toContain('canvas.warn.unpairedParallel');
  });

  it('flags an unpaired LoopBeginNode (null loop_end_node_id)', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'lb',
        node_type: 'LoopBeginNode',
        children: [],
        node_config: { loop_end_node_id: null },
      },
    });
    expect(w.get('node_1')).toContain('canvas.warn.unpairedLoop');
  });

  it('a PAIRED ParallelStart/End → no pairing warning', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'p',
        node_type: 'ParallelStartNode',
        children: [],
        node_config: { branches: {}, parallel_end_node_id: 'node_2' },
      },
      node_2: {
        node_name: 'pe',
        node_type: 'ParallelEndNode',
        children: [],
        node_config: { parallel_start_node_id: 'node_1' },
      },
    });
    expect(w.has('node_1')).toBe(false);
    expect(w.has('node_2')).toBe(false);
  });

  it('flags a dangling reference to a non-existent node_name', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'consumer',
        node_type: 'CodeNode',
        children: [],
        input_fields: {
          x: { type: 'string', value: '', reference: 'ghost.out' },
        },
      },
    });
    expect(w.get('node_1')).toContain('canvas.warn.danglingRefNode');
  });

  it('flags a dangling reference to a missing output_field on a real node', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'producer',
        node_type: 'CodeNode',
        children: ['node_2'],
        output_fields: { real: { type: 'string' } },
      },
      node_2: {
        node_name: 'consumer',
        node_type: 'CodeNode',
        children: [],
        input_fields: {
          x: { type: 'string', value: '', reference: 'producer.missing' },
        },
      },
    });
    expect(w.get('node_2')).toContain('canvas.warn.danglingRefField');
  });

  it('a VALID reference → no dangling warning', () => {
    const w = nodeWarnings({
      node_1: {
        node_name: 'producer',
        node_type: 'CodeNode',
        children: ['node_2'],
        output_fields: { real: { type: 'string' } },
      },
      node_2: {
        node_name: 'consumer',
        node_type: 'CodeNode',
        children: [],
        input_fields: {
          x: { type: 'string', value: '', reference: 'producer.real' },
        },
      },
    });
    expect(w.has('node_2')).toBe(false);
  });

  it('a clean Start→End workflow has no warnings at all', () => {
    const w = nodeWarnings({
      node_1: { node_name: 'start', node_type: 'StartNode', children: ['node_2'] },
      node_2: { node_name: 'end', node_type: 'EndNode', children: [] },
    });
    expect(w.size).toBe(0);
  });
});
