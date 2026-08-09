import { describe, expect, it } from 'vitest';

import {
  defaultColumnsState,
  makeUserColumn,
  reorderUserColumns,
  toWireColumns,
  type UserColumn,
} from '@/pages/canvas/inspector/batch-output-columns-model';

function userCol(patch: Partial<UserColumn>): UserColumn {
  return { ...makeUserColumn(), ...patch };
}

describe('reorderUserColumns', () => {
  const a = userCol({ name: 'a' });
  const b = userCol({ name: 'b' });
  const c = userCol({ name: 'c' });

  it('moves an item forward', () => {
    expect(reorderUserColumns([a, b, c], 0, 2).map((x) => x.name)).toEqual([
      'b',
      'c',
      'a',
    ]);
  });
  it('moves an item backward', () => {
    expect(reorderUserColumns([a, b, c], 2, 0).map((x) => x.name)).toEqual([
      'c',
      'a',
      'b',
    ]);
  });
  it('is a no-op for equal or out-of-range indices', () => {
    const cols = [a, b, c];
    expect(reorderUserColumns(cols, 1, 1)).toBe(cols);
    expect(reorderUserColumns(cols, -1, 0)).toBe(cols);
    expect(reorderUserColumns(cols, 0, 9)).toBe(cols);
  });
});

describe('toWireColumns', () => {
  it('emits the 4 fixed columns in fixed order by default', () => {
    expect(toWireColumns(defaultColumnsState())).toEqual([
      { kind: 'index', name: 'index' },
      { kind: 'status', name: 'status' },
      { kind: 'error', name: 'error' },
      { kind: 'execution_time', name: 'execution_time' },
    ]);
  });

  it('appends complete user field columns after the fixed ones', () => {
    const state = {
      ...defaultColumnsState(),
      userColumns: [
        userCol({ name: 'Score', node: 'n', field: 'score', default: 'N/A' }),
      ],
    };
    expect(toWireColumns(state)[4]).toEqual({
      kind: 'field',
      name: 'Score',
      node: 'n',
      field: 'score',
      default: 'N/A',
    });
  });

  it('omits a blank default and falls back to the node.field name', () => {
    const state = {
      ...defaultColumnsState(),
      userColumns: [userCol({ name: '  ', node: 'n', field: 'f', default: '  ' })],
    };
    const last = toWireColumns(state)[4];
    expect(last).toEqual({ kind: 'field', name: 'n.f', node: 'n', field: 'f' });
    expect('default' in last).toBe(false);
  });

  it('skips user columns with no source chosen', () => {
    const state = {
      ...defaultColumnsState(),
      userColumns: [userCol({ name: 'incomplete' })], // no node/field
    };
    expect(toWireColumns(state)).toHaveLength(4);
  });

  it('honours user-edited fixed names', () => {
    const state = defaultColumnsState();
    state.fixedNames.index = 'row_no';
    expect(toWireColumns(state)[0]).toEqual({ kind: 'index', name: 'row_no' });
  });
});
