import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import '@/lib/i18n';
import userEvent from '@testing-library/user-event';

// Avoid the live enums query (msw errors on unhandled requests). The
// component falls back to a static list anyway; mocking keeps it deterministic.
vi.mock('@/lib/api/queries/enums', () => ({
  useEnums: () => ({ data: { field_types: ['string', 'number', 'boolean', 'object'] } }),
  getEnumList: (enums: Record<string, unknown> | undefined, key: string) =>
    Array.isArray(enums?.[key]) ? (enums![key] as string[]) : [],
}));

import {
  FieldsEditor,
  type FieldsMap,
} from '@/pages/canvas/inspector/FieldsEditor';
import { reorderFields } from '@/pages/canvas/inspector/reorder-fields';

describe('reorderFields', () => {
  it('moves a key to a new index, preserving order', () => {
    const f: FieldsMap = {
      a: { type: 'string' },
      b: { type: 'number' },
      c: { type: 'boolean' },
    };
    expect(Object.keys(reorderFields(f, 0, 2))).toEqual(['b', 'c', 'a']);
    expect(Object.keys(reorderFields(f, 2, 0))).toEqual(['c', 'a', 'b']);
  });

  it('returns the same reference on a no-op / out-of-range', () => {
    const f: FieldsMap = { a: { type: 'string' } };
    expect(reorderFields(f, 0, 0)).toBe(f);
    expect(reorderFields(f, 0, 5)).toBe(f);
  });
});

describe('FieldsEditor read-only fields stay copyable', () => {
  it('a read-only (fixed/locked) field name uses readOnly, NOT disabled, so its text is selectable + copyable', () => {
    render(
      <FieldsEditor
        title="Out"
        mode="output"
        fields={{ status_code: { type: 'integer', description: '' } }}
        onChange={vi.fn()}
        readOnly
      />,
    );
    const nameInput = screen.getByTestId('field-name-status_code') as HTMLInputElement;
    // Disabled inputs can't be selected/copied — the fix is the readOnly attr.
    expect(nameInput).not.toBeDisabled();
    expect(nameInput).toHaveAttribute('readonly');
    expect(nameInput.value).toBe('status_code');
  });
});

describe('FieldsEditor (input cards)', () => {
  it('renders one card per field with type + name + value', () => {
    render(
      <FieldsEditor
        title="Input fields"
        mode="input"
        fields={{ foo: { type: 'string', value: 'x', reference: '' } }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('field-card-foo')).toBeInTheDocument();
    expect(screen.getByTestId('field-type-foo')).toBeInTheDocument();
    expect(screen.getByTestId('field-name-foo')).toBeInTheDocument();
    expect(screen.getByTestId('field-foo-value')).toBeInTheDocument();
  });

  it('Add field appends a new field_N card', async () => {
    const onChange = vi.fn();
    render(<FieldsEditor title="In" mode="input" fields={{}} onChange={onChange} />);
    await userEvent.click(screen.getByTestId('add-field-input'));
    expect(onChange).toHaveBeenCalledWith({
      field_1: { type: 'string', value: '', reference: '' },
    });
  });

  it('Ref↔Preset toggle persists reference vs value', async () => {
    const onChange = vi.fn();
    render(
      <FieldsEditor
        title="In"
        mode="input"
        fields={{ foo: { type: 'string', value: 'lit', reference: '' } }}
        referenceCandidates={['start.q']}
        onChange={onChange}
      />,
    );
    // toggle to reference → onChange with a reference, value preserved
    await userEvent.click(screen.getByTestId('field-foo-ref-toggle'));
    expect(onChange).toHaveBeenCalledWith({
      foo: { type: 'string', value: 'lit', reference: 'start.q' },
    });
  });

  it('remove (×) drops the field', async () => {
    const onChange = vi.fn();
    render(
      <FieldsEditor
        title="In"
        mode="input"
        fields={{ a: { type: 'string' }, b: { type: 'number' } }}
        onChange={onChange}
      />,
    );
    await userEvent.click(screen.getByLabelText('remove field a'));
    expect(onChange).toHaveBeenCalledWith({ b: { type: 'number' } });
  });

  it('rejects a duplicate rename inline (no onChange)', async () => {
    const onChange = vi.fn();
    render(
      <FieldsEditor
        title="In"
        mode="input"
        fields={{ a: { type: 'string' }, b: { type: 'number' } }}
        onChange={onChange}
      />,
    );
    const nameInput = screen.getByTestId('field-name-a');
    await userEvent.clear(nameInput);
    await userEvent.type(nameInput, 'b');
    await userEvent.tab();
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/already used/i)).toBeInTheDocument();
  });

  it('reorders via drag handlers (rebuilds key order)', () => {
    const onChange = vi.fn();
    render(
      <FieldsEditor
        title="In"
        mode="input"
        fields={{ a: { type: 'string' }, b: { type: 'number' } }}
        onChange={onChange}
      />,
    );
    // Simulate HTML5 DnD: drag the 2nd handle (index 1), drop on the 1st card.
    fireEvent.dragStart(screen.getByTestId('field-drag-b'));
    fireEvent.dragOver(screen.getByTestId('field-card-a'));
    fireEvent.drop(screen.getByTestId('field-card-a'));
    expect(onChange).toHaveBeenCalledWith({
      b: { type: 'number' },
      a: { type: 'string' },
    });
  });
});

describe('FieldsEditor (output mirror)', () => {
  it('renders read-only mirror + hides controls when outputsFollowInputs', () => {
    render(
      <FieldsEditor
        title="Output fields"
        mode="output"
        outputsFollowInputs
        fields={{ q: { type: 'string' }, n: { type: 'number' } }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('outputs-mirror-caption')).toHaveTextContent(
      /mirror your inputs/i,
    );
    expect(screen.getByTestId('field-mirror-name-q')).toHaveTextContent('q');
    expect(screen.getByTestId('field-mirror-type-n')).toHaveTextContent('number');
    // no add / remove / drag controls
    expect(screen.queryByTestId('add-field-output')).toBeNull();
    expect(screen.queryByLabelText('remove field q')).toBeNull();
    expect(screen.queryByTestId('field-drag-q')).toBeNull();
  });

  it('normal output cards show name + type + description', () => {
    render(
      <FieldsEditor
        title="Output fields"
        mode="output"
        fields={{ out1: { type: 'string', description: 'hi' } }}
        onChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('field-description-out1')).toBeInTheDocument();
    expect(screen.getByTestId('add-field-output')).toBeInTheDocument();
  });
});
