import { describe, it, expect } from 'vitest';
import { nodeNameOf, nodeLabel, humanizeNodeRefs } from '@/lib/workflow/node-label';

const draft = {
  node_3: { node_name: 'my_prompt' },
  node_4: { node_name: 'node_4' }, // name equals id
  node_5: {}, // no name
} as const;

describe('nodeNameOf', () => {
  it('returns the node_name when present', () => {
    expect(nodeNameOf(draft, 'node_3')).toBe('my_prompt');
  });
  it('returns null when missing or draft is not an object', () => {
    expect(nodeNameOf(draft, 'node_5')).toBeNull();
    expect(nodeNameOf(draft, 'node_99')).toBeNull();
    expect(nodeNameOf(null, 'node_3')).toBeNull();
    expect(nodeNameOf([], 'node_3')).toBeNull();
  });
});

describe('nodeLabel', () => {
  it('returns name(node_id) when named', () => {
    expect(nodeLabel(draft, 'node_3')).toBe('my_prompt(node_3)');
  });
  it('returns just the id when unnamed', () => {
    expect(nodeLabel(draft, 'node_5')).toBe('node_5');
    expect(nodeLabel(draft, 'node_99')).toBe('node_99');
  });
  it('returns just the id when name equals id', () => {
    expect(nodeLabel(draft, 'node_4')).toBe('node_4');
  });
});

describe('humanizeNodeRefs', () => {
  it('rewrites known ids and leaves unknown ones untouched', () => {
    const msg = '[NodeId: node_3][PromptNode]: bad';
    expect(humanizeNodeRefs(msg, draft)).toBe(
      '[NodeId: my_prompt(node_3)][PromptNode]: bad',
    );
    expect(humanizeNodeRefs('something about node_9 here', draft)).toBe(
      'something about node_9 here',
    );
  });
  it('handles empty text and multiple ids', () => {
    expect(humanizeNodeRefs('', draft)).toBe('');
    expect(humanizeNodeRefs('node_3 -> node_5', draft)).toBe(
      'my_prompt(node_3) -> node_5',
    );
  });
});
