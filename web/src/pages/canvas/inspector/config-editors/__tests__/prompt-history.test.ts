/**
 * Unit tests for the PromptNode prompt-history stepping helpers
 * (`prompt-history.ts`). Pure logic only — `getPromptAt` is injected, so no
 * network / React Query is involved.
 */
import { describe, expect, it } from 'vitest';
import {
  configFieldAtVersion,
  promptAtVersion,
  sortVersionsNewestFirst,
  stepToDifferingVersion,
  versionLabel,
  type WorkflowVersionRef,
} from '../prompt-history';

describe('promptAtVersion', () => {
  it('reads node_config.prompt_template when present', () => {
    const wf = {
      node_1: { node_config: { prompt_template: 'hello {{x}}' } },
    };
    expect(promptAtVersion(wf, 'node_1')).toBe('hello {{x}}');
  });

  it('returns "" when the node is absent at that version', () => {
    expect(promptAtVersion({ node_2: {} }, 'node_1')).toBe('');
  });

  it('returns "" when the field is missing / non-string / workflow null', () => {
    expect(promptAtVersion({ node_1: { node_config: {} } }, 'node_1')).toBe('');
    expect(
      promptAtVersion({ node_1: { node_config: { prompt_template: 42 } } }, 'node_1'),
    ).toBe('');
    expect(promptAtVersion(null, 'node_1')).toBe('');
    expect(promptAtVersion(undefined, 'node_1')).toBe('');
  });
});

describe('configFieldAtVersion (generalized field)', () => {
  it('reads an arbitrary node_config field (e.g. TemplateNode template)', () => {
    const wf = {
      node_1: { node_config: { template: '<h1>{{t}}</h1>', prompt_template: 'p' } },
    };
    expect(configFieldAtVersion(wf, 'node_1', 'template')).toBe('<h1>{{t}}</h1>');
    expect(configFieldAtVersion(wf, 'node_1', 'prompt_template')).toBe('p');
  });

  it('returns "" for missing field / node / non-string / nullish workflow', () => {
    expect(configFieldAtVersion({ node_1: { node_config: {} } }, 'node_1', 'template')).toBe('');
    expect(configFieldAtVersion({ node_2: {} }, 'node_1', 'template')).toBe('');
    expect(
      configFieldAtVersion({ node_1: { node_config: { template: 7 } } }, 'node_1', 'template'),
    ).toBe('');
    expect(configFieldAtVersion(null, 'node_1', 'template')).toBe('');
  });
});

describe('versionLabel', () => {
  it('formats v{major}.sv{sub}', () => {
    expect(versionLabel({ major: 2, sub: 3 })).toBe('v2.sv3');
  });
});

describe('sortVersionsNewestFirst', () => {
  it('orders by major desc then sub desc, without mutating input', () => {
    const input: WorkflowVersionRef[] = [
      { major: 1, sub: 0 },
      { major: 2, sub: 0 },
      { major: 1, sub: 1 },
    ];
    const out = sortVersionsNewestFirst(input);
    expect(out.map(versionLabel)).toEqual(['v2.sv0', 'v1.sv1', 'v1.sv0']);
    // input untouched
    expect(input.map(versionLabel)).toEqual(['v1.sv0', 'v2.sv0', 'v1.sv1']);
  });
});

describe('stepToDifferingVersion', () => {
  // Newest → oldest. Prompts chosen so indices 1 & 2 are identical to the
  // newer neighbour (must be skipped), index 3 differs, index 4 == index 3.
  //   idx: 0    1    2    3    4
  //   p  : "A"  "B"  "B"  "C"  "C"
  const versions: WorkflowVersionRef[] = [
    { major: 5, sub: 0 },
    { major: 4, sub: 0 },
    { major: 3, sub: 0 },
    { major: 2, sub: 0 },
    { major: 1, sub: 0 },
  ];
  const prompts = ['A', 'B', 'B', 'C', 'C'];
  const getPromptAt = (i: number) => prompts[i];

  it('older from 0 lands on the first DIFFERING version (idx 1, skips none)', () => {
    expect(stepToDifferingVersion(versions, 0, 'older', getPromptAt)).toBe(1);
  });

  it('older from 1 SKIPS the identical idx 2 and lands on idx 3', () => {
    expect(stepToDifferingVersion(versions, 1, 'older', getPromptAt)).toBe(3);
  });

  it('older from 3 returns null (idx 4 is identical, then end → disable)', () => {
    expect(stepToDifferingVersion(versions, 3, 'older', getPromptAt)).toBeNull();
  });

  it('older from the last index returns null', () => {
    expect(stepToDifferingVersion(versions, 4, 'older', getPromptAt)).toBeNull();
  });

  it('newer from 3 lands on idx 2 (first differing toward newer)', () => {
    // idx 3 == "C"; idx 2 == "B" already differs → no skip needed.
    expect(stepToDifferingVersion(versions, 3, 'newer', getPromptAt)).toBe(2);
  });

  it('newer from 4 SKIPS the identical idx 3 and lands on the differing idx 2', () => {
    // idx 4 == "C"; idx 3 == "C" (skip); idx 2 == "B" differs.
    expect(stepToDifferingVersion(versions, 4, 'newer', getPromptAt)).toBe(2);
  });

  it('newer from 1 lands on the differing idx 0', () => {
    expect(stepToDifferingVersion(versions, 1, 'newer', getPromptAt)).toBe(0);
  });

  it('newer from 0 returns null (already newest → disable)', () => {
    expect(stepToDifferingVersion(versions, 0, 'newer', getPromptAt)).toBeNull();
  });

  it('returns null for an out-of-range current index', () => {
    expect(stepToDifferingVersion(versions, -1, 'older', getPromptAt)).toBeNull();
    expect(stepToDifferingVersion(versions, 99, 'older', getPromptAt)).toBeNull();
  });

  it('all-identical chain → both directions null', () => {
    const flat = () => 'same';
    expect(stepToDifferingVersion(versions, 2, 'older', flat)).toBeNull();
    expect(stepToDifferingVersion(versions, 2, 'newer', flat)).toBeNull();
  });
});
