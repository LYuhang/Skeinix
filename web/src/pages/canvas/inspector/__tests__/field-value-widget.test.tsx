import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  FieldValueWidget,
} from '@/pages/canvas/inspector/FieldValueWidget';
import {
  coerceValueForType,
  valueToDisplayString,
  FieldCoercionError,
} from '@/pages/canvas/inspector/field-value-model';

describe('coerceValueForType', () => {
  it('coerces number / integer', () => {
    expect(coerceValueForType('5', 'number')).toBe(5);
    expect(coerceValueForType('5.5', 'number')).toBe(5.5);
    expect(coerceValueForType('7.9', 'integer')).toBe(7);
    expect(coerceValueForType(3, 'number')).toBe(3);
  });

  it('throws on non-numeric number', () => {
    expect(() => coerceValueForType('abc', 'number')).toThrow(FieldCoercionError);
    expect(() => coerceValueForType('', 'integer')).toThrow(FieldCoercionError);
  });

  it('coerces boolean from bool or string', () => {
    expect(coerceValueForType(true, 'boolean')).toBe(true);
    expect(coerceValueForType('true', 'boolean')).toBe(true);
    expect(coerceValueForType('false', 'boolean')).toBe(false);
  });

  it('parses object / array JSON and throws on malformed', () => {
    expect(coerceValueForType('{"a":1}', 'object')).toEqual({ a: 1 });
    expect(coerceValueForType('[1,2]', 'array')).toEqual([1, 2]);
    expect(coerceValueForType('["u1","u2"]', 'list')).toEqual(['u1', 'u2']);
    expect(coerceValueForType('{"a":1}', 'dict')).toEqual({ a: 1 });
    expect(coerceValueForType('{"a":1}', 'json')).toEqual({ a: 1 });
    expect(coerceValueForType('', 'array')).toEqual([]);
    expect(() => coerceValueForType('{bad', 'object')).toThrow(FieldCoercionError);
    expect(() => coerceValueForType('{"a":1}', 'array')).toThrow(FieldCoercionError);
    expect(() => coerceValueForType('[1,2]', 'object')).toThrow(FieldCoercionError);
    // already-parsed object passes through
    expect(coerceValueForType({ x: 1 }, 'object')).toEqual({ x: 1 });
    expect(coerceValueForType(['x'], 'list')).toEqual(['x']);
  });

  it('passes strings through', () => {
    expect(coerceValueForType('hi', 'string')).toBe('hi');
    expect(coerceValueForType(42, 'string')).toBe('42');
  });
});

describe('valueToDisplayString', () => {
  it('pretty-prints object/array, stringifies primitives', () => {
    expect(valueToDisplayString({ a: 1 }, 'object')).toBe('{\n  "a": 1\n}');
    expect(valueToDisplayString(['a'], 'list')).toBe('[\n  "a"\n]');
    expect(valueToDisplayString(5, 'number')).toBe('5');
    expect(valueToDisplayString(undefined, 'string')).toBe('');
  });
});

describe('FieldValueWidget', () => {
  it('toggles Preset ↔ Reference, emitting the right slot', async () => {
    const onChange = vi.fn();
    render(
      <FieldValueWidget
        type="string"
        value="hello"
        reference=""
        referenceCandidates={['start.user_query', 'fetch.rows']}
        idBase="t1"
        onChange={onChange}
      />,
    );
    // preset mode by default → toggling on switches to reference, seeds first candidate
    await userEvent.click(screen.getByTestId('t1-ref-toggle'));
    expect(onChange).toHaveBeenCalledWith({
      value: 'hello',
      reference: 'start.user_query',
    });
  });

  it('reference mode renders a dropdown of candidates (not free text)', async () => {
    const onChange = vi.fn();
    render(
      <FieldValueWidget
        type="string"
        value=""
        reference="start.user_query"
        referenceCandidates={['start.user_query', 'fetch.rows']}
        idBase="t2"
        onChange={onChange}
      />,
    );
    const select = screen.getByTestId('t2-ref-select') as HTMLSelectElement;
    expect(select.tagName).toBe('SELECT');
    await userEvent.selectOptions(select, 'fetch.rows');
    expect(onChange).toHaveBeenCalledWith({ value: '', reference: 'fetch.rows' });
  });

  it('preset number commits a coerced number; bad input shows an error', async () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <FieldValueWidget type="number" value={undefined} reference="" idBase="n1" onChange={onChange} />,
    );
    const input = screen.getByTestId('n1-input');
    await userEvent.type(input, '12');
    await userEvent.tab();
    expect(onChange).toHaveBeenCalledWith({ value: 12, reference: '' });

    // bad input → error, no commit
    onChange.mockClear();
    rerender(
      <FieldValueWidget type="number" value={undefined} reference="" idBase="n1" onChange={onChange} />,
    );
    const input2 = screen.getByTestId('n1-input');
    await userEvent.type(input2, 'abc');
    await userEvent.tab();
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByTestId('n1-error')).toBeInTheDocument();
  });

  it('preset object textarea parses JSON and blocks on malformed', async () => {
    const onChange = vi.fn();
    render(
      <FieldValueWidget type="object" value={{}} reference="" idBase="o1" onChange={onChange} />,
    );
    const ta = screen.getByTestId('o1-json');
    await userEvent.clear(ta);
    await userEvent.type(ta, '{{"a":1}'); // userEvent escapes "{{" → "{"
    await userEvent.tab();
    expect(onChange).toHaveBeenLastCalledWith({ value: { a: 1 }, reference: '' });
  });

  it('boolean renders a switch and commits a bool', async () => {
    const onChange = vi.fn();
    render(
      <FieldValueWidget type="boolean" value={false} reference="" idBase="b1" onChange={onChange} />,
    );
    await userEvent.click(screen.getByTestId('b1-checkbox'));
    expect(onChange).toHaveBeenCalledWith({ value: true, reference: '' });
  });

  it('allowReference=false hides the toggle (preset only)', () => {
    render(
      <FieldValueWidget
        type="string"
        value=""
        reference=""
        allowReference={false}
        idBase="p1"
        onChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('p1-ref-toggle')).toBeNull();
    expect(screen.getByTestId('p1-input')).toBeInTheDocument();
  });

  it('respects readOnly', () => {
    render(
      <FieldValueWidget type="string" value="x" reference="" idBase="r1" readOnly onChange={vi.fn()} />,
    );
    expect(screen.getByTestId('r1-input')).toBeDisabled();
  });
});
