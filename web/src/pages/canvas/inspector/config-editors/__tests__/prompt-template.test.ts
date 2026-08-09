/**
 * Unit tests for the pure `missingOutputFields` helper — the testable core
 * of the PromptNode output-field presence check.
 *
 * A field counts as PRESENT iff the template references it as a quoted
 * string (`"name"` or `'name'`); a bare un-quoted mention does NOT count.
 */
import { describe, expect, it } from 'vitest';
import { missingOutputFields } from '../prompt-template';

describe('missingOutputFields', () => {
  it('treats a double-quoted "name" as present (not missing)', () => {
    expect(missingOutputFields('return {"answer": 1}', ['answer'])).toEqual([]);
  });

  it('treats a single-quoted \'name\' as present (not missing)', () => {
    expect(missingOutputFields("return {'answer': 1}", ['answer'])).toEqual([]);
  });

  it('reports an absent field as missing', () => {
    expect(missingOutputFields('return {"other": 1}', ['answer'])).toEqual([
      'answer',
    ]);
  });

  it('a bare un-quoted word does NOT count as present', () => {
    expect(missingOutputFields('the answer is 42', ['answer'])).toEqual([
      'answer',
    ]);
  });

  it('empty template → all fields missing (in order)', () => {
    expect(missingOutputFields('', ['a', 'b'])).toEqual(['a', 'b']);
  });

  it('no output fields → nothing missing', () => {
    expect(missingOutputFields('"a" "b"', [])).toEqual([]);
  });

  it('mixed present/absent returns only the absent ones', () => {
    const tpl = 'output "score" and \'label\' here';
    expect(missingOutputFields(tpl, ['score', 'label', 'reason'])).toEqual([
      'reason',
    ]);
  });

  it('escapes regex-special characters in field names', () => {
    // A field literally named "a.b" must NOT match "axb" via the regex dot.
    expect(missingOutputFields('"axb"', ['a.b'])).toEqual(['a.b']);
    expect(missingOutputFields('"a.b"', ['a.b'])).toEqual([]);
  });

  it('ignores empty field names', () => {
    expect(missingOutputFields('anything', [''])).toEqual([]);
  });
});
