import { describe, expect, it } from 'vitest';
import type { Edge, Node } from '@xyflow/react';
import { autoLayout } from '@/pages/canvas/auto-layout';

describe('autoLayout (left-to-right)', () => {
  it('lays a 2-node chain A->B horizontally (B to the right of A)', () => {
    const nodes: Node[] = [
      { id: 'a', position: { x: 0, y: 0 }, data: {} },
      { id: 'b', position: { x: 0, y: 0 }, data: {} },
    ];
    const edges: Edge[] = [{ id: 'e', source: 'a', target: 'b' }];
    const out = autoLayout(nodes, edges);
    const a = out.find((n) => n.id === 'a')!;
    const b = out.find((n) => n.id === 'b')!;
    // LR: the downstream node advances along X, not Y.
    expect(b.position.x).toBeGreaterThan(a.position.x);
    expect(Math.abs(b.position.y - a.position.y)).toBeLessThan(Math.abs(b.position.x - a.position.x));
  });

  it('places parallel siblings on the same rank (same x), spread along y', () => {
    const nodes: Node[] = [
      { id: 'root', position: { x: 0, y: 0 }, data: {} },
      { id: 's1', position: { x: 0, y: 0 }, data: {} },
      { id: 's2', position: { x: 0, y: 0 }, data: {} },
    ];
    const edges: Edge[] = [
      { id: 'e1', source: 'root', target: 's1' },
      { id: 'e2', source: 'root', target: 's2' },
    ];
    const out = autoLayout(nodes, edges);
    const s1 = out.find((n) => n.id === 's1')!;
    const s2 = out.find((n) => n.id === 's2')!;
    // siblings share a rank → same x, different y (vertical fan-out, horizontal flow).
    expect(s1.position.x).toBeCloseTo(s2.position.x, 0);
    expect(s1.position.y).not.toBeCloseTo(s2.position.y, 0);
  });
});
