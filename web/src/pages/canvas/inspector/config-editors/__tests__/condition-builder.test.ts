/** Stream 3 — pure condition-builder expression generation. */
import { describe, expect, it } from 'vitest';
import { buildConditionStr, toPyLiteral } from '../condition-builder';

describe('toPyLiteral', () => {
  it('renders numbers bare', () => {
    expect(toPyLiteral('0.8')).toBe('0.8');
    expect(toPyLiteral('42')).toBe('42');
    expect(toPyLiteral('-3')).toBe('-3');
  });
  it('renders booleans Python-style', () => {
    expect(toPyLiteral('true')).toBe('True');
    expect(toPyLiteral('False')).toBe('False');
  });
  it('quotes + escapes strings', () => {
    expect(toPyLiteral('urgent')).toBe("'urgent'");
    expect(toPyLiteral("o'clock")).toBe("'o\\'clock'");
  });
  it('empty → empty-string literal', () => {
    expect(toPyLiteral('')).toBe("''");
  });
});

describe('buildConditionStr', () => {
  it('comparison ops → `{field} op literal`', () => {
    expect(buildConditionStr('score', '>=', '0.8')).toBe('{score} >= 0.8');
    expect(buildConditionStr('category', '==', 'urgent')).toBe(
      "{category} == 'urgent'",
    );
  });
  it('contains → `literal in {field}`', () => {
    expect(buildConditionStr('tags', 'contains', 'vip')).toBe("'vip' in {tags}");
  });
  it('in → `{field} in literal`', () => {
    expect(buildConditionStr('status', 'in', "['a','b']")).toBe(
      "{status} in '[\\'a\\',\\'b\\']'",
    );
  });
  it('empty field → empty string', () => {
    expect(buildConditionStr('', '==', 'x')).toBe('');
  });
});
