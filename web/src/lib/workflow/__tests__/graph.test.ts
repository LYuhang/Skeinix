/**
 * Unit tests for `ancestorsOf` — the reverse (upstream) traversal that powers
 * the input-field Reference picker (a node may only reference the output of
 * nodes that run BEFORE it).
 */
import { describe, expect, it } from 'vitest';
import {
  ancestorsOf,
  getNodeLoopMemberships,
  referenceCandidatesFromAncestors,
} from '@/lib/workflow/graph';

describe('ancestorsOf', () => {
  it('linear chain: start → a → b → end — b sees {start, a}, never end', () => {
    const draft = {
      start: { children: ['a'] },
      a: { children: ['b'] },
      b: { children: ['end'] },
      end: { children: [] },
      __meta__: {},
    };
    expect(ancestorsOf(draft, 'b')).toEqual(new Set(['start', 'a']));
    // never includes the node itself or its descendant `end`.
    expect(ancestorsOf(draft, 'b').has('b')).toBe(false);
    expect(ancestorsOf(draft, 'b').has('end')).toBe(false);
    // start has no predecessors.
    expect(ancestorsOf(draft, 'start')).toEqual(new Set());
    // end sees the whole chain upstream.
    expect(ancestorsOf(draft, 'end')).toEqual(new Set(['start', 'a', 'b']));
  });

  it('diamond: ancestors include BOTH branch upstreams, exclude siblings/descendants', () => {
    //        start
    //        /    \
    //       a      b
    //        \    /
    //         join
    //          |
    //         end
    const draft = {
      start: { children: ['a', 'b'] },
      a: { children: ['join'] },
      b: { children: ['join'] },
      join: { children: ['end'] },
      end: { children: [] },
    };
    expect(ancestorsOf(draft, 'join')).toEqual(new Set(['start', 'a', 'b']));
    // a's ancestor is only start — NOT its sibling b, NOT its descendant join.
    expect(ancestorsOf(draft, 'a')).toEqual(new Set(['start']));
    expect(ancestorsOf(draft, 'a').has('b')).toBe(false);
    expect(ancestorsOf(draft, 'a').has('join')).toBe(false);
    expect(ancestorsOf(draft, 'end')).toEqual(
      new Set(['start', 'a', 'b', 'join']),
    );
  });

  it('returns an empty set for a null draft or an absent node', () => {
    expect(ancestorsOf(null, 'x')).toEqual(new Set());
    expect(ancestorsOf({ start: { children: [] } }, 'missing')).toEqual(
      new Set(),
    );
  });

  it('is cycle-safe (malformed non-DAG does not loop forever)', () => {
    const draft = {
      a: { children: ['b'] },
      b: { children: ['a'] },
    };
    expect(ancestorsOf(draft, 'a')).toEqual(new Set(['a', 'b']));
  });
});

/** Convenience: a node with one output field named `out`. */
function nodeWithOut(
  name: string,
  children: string[],
  extra: Record<string, unknown> = {},
) {
  return {
    node_name: name,
    children,
    output_fields: { out: { type: 'string' } },
    ...extra,
  };
}

describe('getNodeLoopMemberships', () => {
  it('marks only interior body nodes — boundaries B and E are NOT members', () => {
    // start → B → body1 → body2 → E → after
    const draft = {
      start: nodeWithOut('Start', ['B']),
      B: nodeWithOut('Begin', ['body1'], {
        node_type: 'LoopBeginNode',
        node_config: { loop_end_node_id: 'E' },
      }),
      body1: nodeWithOut('Body1', ['body2']),
      body2: nodeWithOut('Body2', ['E']),
      E: nodeWithOut('End', ['after'], {
        node_type: 'LoopEndNode',
        node_config: { loop_begin_node_id: 'B' },
      }),
      after: nodeWithOut('After', []),
      __meta__: {},
    };
    const m = getNodeLoopMemberships(draft);
    expect(m.get('body1')).toEqual(new Set(['B']));
    expect(m.get('body2')).toEqual(new Set(['B']));
    // boundaries + outside nodes are not members of loop B.
    expect(m.get('B')).toBeUndefined();
    expect(m.get('E')).toBeUndefined();
    expect(m.get('start')).toBeUndefined();
    expect(m.get('after')).toBeUndefined();
  });

  it('nested loops: inner-body node carries BOTH begin ids; inner begin carries only outer', () => {
    // start → BO → BI → inner → EI → outerTail → EO → after
    const draft = {
      start: nodeWithOut('Start', ['BO']),
      BO: nodeWithOut('OuterBegin', ['BI'], {
        node_type: 'LoopBeginNode',
        node_config: { loop_end_node_id: 'EO' },
      }),
      BI: nodeWithOut('InnerBegin', ['inner'], {
        node_type: 'LoopBeginNode',
        node_config: { loop_end_node_id: 'EI' },
      }),
      inner: nodeWithOut('Inner', ['EI']),
      EI: nodeWithOut('InnerEnd', ['outerTail'], {
        node_type: 'LoopEndNode',
        node_config: { loop_begin_node_id: 'BI' },
      }),
      outerTail: nodeWithOut('OuterTail', ['EO']),
      EO: nodeWithOut('OuterEnd', ['after'], {
        node_type: 'LoopEndNode',
        node_config: { loop_begin_node_id: 'BO' },
      }),
      after: nodeWithOut('After', []),
    };
    const m = getNodeLoopMemberships(draft);
    // inner-body node is inside BOTH loops.
    expect(m.get('inner')).toEqual(new Set(['BO', 'BI']));
    // inner begin is a boundary of BI but interior of BO.
    expect(m.get('BI')).toEqual(new Set(['BO']));
    // tail after the inner loop's end, still in outer body.
    expect(m.get('outerTail')).toEqual(new Set(['BO']));
    // inner end is a boundary of BI, interior of BO.
    expect(m.get('EI')).toEqual(new Set(['BO']));
    // outer boundaries are not members of either loop.
    expect(m.get('BO')).toBeUndefined();
    expect(m.get('EO')).toBeUndefined();
  });

  it('skips a loop whose loop_end_node_id is null/absent (no scoping)', () => {
    const draft = {
      B: nodeWithOut('Begin', ['body'], {
        node_type: 'LoopBeginNode',
        node_config: { loop_end_node_id: null },
      }),
      body: nodeWithOut('Body', []),
    };
    expect(getNodeLoopMemberships(draft).size).toBe(0);
  });

  it('returns an empty map for a null draft', () => {
    expect(getNodeLoopMemberships(null).size).toBe(0);
  });
});

describe('referenceCandidatesFromAncestors — loop-scope filtering', () => {
  it('no loops: offers every ancestor output (unchanged behavior)', () => {
    const draft = {
      start: nodeWithOut('Start', ['a']),
      a: nodeWithOut('A', ['b']),
      b: nodeWithOut('B', ['end']),
      end: nodeWithOut('End', []),
    };
    expect(referenceCandidatesFromAncestors(draft, 'b').sort()).toEqual(
      ['A.out', 'Start.out'].sort(),
    );
  });

  it('self AFTER a loop: hides body nodes, offers the loop_begin loop_output/i', () => {
    // start → B → body → E → self
    const draft = {
      start: nodeWithOut('Start', ['B']),
      B: {
        node_name: 'Begin',
        node_type: 'LoopBeginNode',
        node_config: { loop_end_node_id: 'E' },
        children: ['body'],
        output_fields: { loop_output: { type: 'array' }, i: { type: 'int' } },
      },
      body: nodeWithOut('Body', ['E']),
      E: {
        node_name: 'End',
        node_type: 'LoopEndNode',
        node_config: { loop_begin_node_id: 'B' },
        children: ['self'],
        output_fields: {},
      },
      self: nodeWithOut('Self', []),
    };
    const cands = referenceCandidatesFromAncestors(draft, 'self');
    // body output is out of scope.
    expect(cands).not.toContain('Body.out');
    // the loop_begin aggregated outputs ARE offered.
    expect(cands).toContain('Begin.loop_output');
    expect(cands).toContain('Begin.i');
    // node before the loop is still offered.
    expect(cands).toContain('Start.out');
  });

  it('self INSIDE the body: sees loop_begin outputs AND earlier body siblings', () => {
    // start → B → body1 → self → E
    const draft = {
      start: nodeWithOut('Start', ['B']),
      B: {
        node_name: 'Begin',
        node_type: 'LoopBeginNode',
        node_config: { loop_end_node_id: 'E' },
        children: ['body1'],
        output_fields: { loop_output: { type: 'array' }, i: { type: 'int' } },
      },
      body1: nodeWithOut('Body1', ['self']),
      self: nodeWithOut('Self', ['E']),
      E: {
        node_name: 'End',
        node_type: 'LoopEndNode',
        node_config: { loop_begin_node_id: 'B' },
        children: [],
        output_fields: {},
      },
    };
    const cands = referenceCandidatesFromAncestors(draft, 'self');
    expect(cands).toContain('Body1.out'); // same scope sibling
    expect(cands).toContain('Begin.i'); // loop begin boundary
    expect(cands).toContain('Begin.loop_output');
    expect(cands).toContain('Start.out');
  });

  it('nested: inner-body node hidden from outer self; inner begin offered; from fully-outside only OUTER begin', () => {
    // start → BO → BI → inner → EI → outerTail → EO → after
    const draft = {
      start: nodeWithOut('Start', ['BO']),
      BO: {
        node_name: 'OuterBegin',
        node_type: 'LoopBeginNode',
        node_config: { loop_end_node_id: 'EO' },
        children: ['BI'],
        output_fields: { loop_output: { type: 'array' } },
      },
      BI: {
        node_name: 'InnerBegin',
        node_type: 'LoopBeginNode',
        node_config: { loop_end_node_id: 'EI' },
        children: ['inner'],
        output_fields: { loop_output: { type: 'array' } },
      },
      inner: nodeWithOut('Inner', ['EI']),
      EI: {
        node_name: 'InnerEnd',
        node_type: 'LoopEndNode',
        node_config: { loop_begin_node_id: 'BI' },
        children: ['outerTail'],
        output_fields: {},
      },
      outerTail: nodeWithOut('OuterTail', ['EO']),
      EO: {
        node_name: 'OuterEnd',
        node_type: 'LoopEndNode',
        node_config: { loop_begin_node_id: 'BO' },
        children: ['after'],
        output_fields: {},
      },
      after: nodeWithOut('After', []),
    };

    // outerTail is in the OUTER body, after the inner loop's end.
    const outerSelf = referenceCandidatesFromAncestors(draft, 'outerTail');
    expect(outerSelf).not.toContain('Inner.out'); // inner-body node hidden
    expect(outerSelf).toContain('InnerBegin.loop_output'); // inner begin offered
    expect(outerSelf).toContain('OuterBegin.loop_output');
    expect(outerSelf).toContain('Start.out');

    // `after` is fully outside both loops.
    const outsideSelf = referenceCandidatesFromAncestors(draft, 'after');
    expect(outsideSelf).not.toContain('Inner.out');
    expect(outsideSelf).not.toContain('InnerBegin.loop_output'); // inner begin hidden
    expect(outsideSelf).not.toContain('OuterTail.out');
    expect(outsideSelf).toContain('OuterBegin.loop_output'); // only outer begin
    expect(outsideSelf).toContain('Start.out');
  });

  it('incomplete loop (loop_end_node_id null): degrades gracefully, no scoping applied', () => {
    // start → B → body → self  (no end, so B never bounds a body)
    const draft = {
      start: nodeWithOut('Start', ['B']),
      B: {
        node_name: 'Begin',
        node_type: 'LoopBeginNode',
        node_config: { loop_end_node_id: null },
        children: ['body'],
        output_fields: { loop_output: { type: 'array' } },
      },
      body: nodeWithOut('Body', ['self']),
      self: nodeWithOut('Self', []),
    };
    const cands = referenceCandidatesFromAncestors(draft, 'self');
    // no scoping → body still offered.
    expect(cands).toContain('Body.out');
    expect(cands).toContain('Begin.loop_output');
    expect(cands).toContain('Start.out');
  });
});
