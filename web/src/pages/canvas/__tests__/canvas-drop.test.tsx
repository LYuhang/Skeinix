import { describe, it, expect, vi, beforeEach } from 'vitest';
import { handleNodeDrop } from '@/pages/canvas/Canvas';

const addNode = vi.fn();
const screenToFlowPosition = ({ x, y }: { x: number; y: number }) => ({ x: x + 1, y: y + 1 });

function makeEvent(raw: string | undefined, clientX = 10, clientY = 20) {
  const preventDefault = vi.fn();
  const dataTransfer = {
    getData: (fmt: string) =>
      fmt === 'application/vibecanvas-node' && raw !== undefined ? raw : '',
    dropEffect: '',
  } as unknown as DataTransfer;
  return { dataTransfer, clientX, clientY, preventDefault } as const;
}

beforeEach(() => addNode.mockClear());

describe('handleNodeDrop', () => {
  it('inserts a dropped node at screenToFlowPosition(cursor)', () => {
    const payload = { node_type: 'PromptNode' };
    const ev = makeEvent(JSON.stringify(payload), 10, 20);
    handleNodeDrop(ev, { readOnly: false, screenToFlowPosition, addNode });
    expect(ev.preventDefault).toHaveBeenCalled();
    expect(addNode).toHaveBeenCalledWith(payload, { x: 11, y: 21 });
  });

  it('readOnly ignores drops', () => {
    const ev = makeEvent(JSON.stringify({ node_type: 'X' }), 1, 1);
    handleNodeDrop(ev, { readOnly: true, screenToFlowPosition, addNode });
    expect(addNode).not.toHaveBeenCalled();
  });

  it('ignores drops with no payload', () => {
    const ev = makeEvent(undefined);
    handleNodeDrop(ev, { readOnly: false, screenToFlowPosition, addNode });
    expect(addNode).not.toHaveBeenCalled();
  });

  it('ignores drops with malformed JSON', () => {
    const ev = makeEvent('not-json{');
    handleNodeDrop(ev, { readOnly: false, screenToFlowPosition, addNode });
    expect(addNode).not.toHaveBeenCalled();
  });
});
