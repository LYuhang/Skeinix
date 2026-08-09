import { describe, it, expect } from 'vitest';
import { formatCheckError } from '@/components/modals/formatCheckError';

describe('formatCheckError', () => {
  it('falls back to a generic headline on empty input', () => {
    expect(formatCheckError(null)).toEqual({ headline: 'Check failed', detail: '' });
    expect(formatCheckError('   ')).toEqual({ headline: 'Check failed', detail: '' });
  });

  it('splits the [X Check]: prefix into headline + detail', () => {
    const r = formatCheckError("[StartNode Check]: 'node_name' was expected to be '__start__'");
    expect(r.headline).toBe('StartNode Check');
    expect(r.detail).toBe("'node_name' was expected to be '__start__'");
  });

  it('keeps an unprefixed message as the whole detail', () => {
    const r = formatCheckError('Workflow must contain exactly one StartNode, found 2.');
    expect(r.headline).toBe('Check failed');
    expect(r.detail).toBe('Workflow must contain exactly one StartNode, found 2.');
  });

  it('adds a process_fn hint for CodeNode body errors', () => {
    const r = formatCheckError(
      "The provided code must explicitly define a function named 'process_fn'.",
    );
    expect(r.hint).toMatch(/process_fn/);
  });

  it('adds a reserved-name hint for a renamed StartNode', () => {
    const r = formatCheckError("[StartNode Check]: 'node_name' const was '__start__'");
    expect(r.hint).toMatch(/__start__/);
  });

  it('adds a one-StartNode hint', () => {
    const r = formatCheckError('Workflow must contain exactly one StartNode, found 0.');
    expect(r.hint).toMatch(/exactly one Start node/);
  });

  it('adds an isolated-node hint', () => {
    const r = formatCheckError(
      "Found isolated nodes unreachable from StartNode: {'node_3'}",
    );
    expect(r.hint).toMatch(/reachable from the Start node/);
  });

  it('leaves hint undefined for an unrecognized error', () => {
    expect(formatCheckError('something totally novel').hint).toBeUndefined();
  });
});
