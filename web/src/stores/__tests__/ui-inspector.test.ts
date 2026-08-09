/**
 * Inspector scope and tab state machine in `useUIStore`.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useUIStore } from '@/stores/ui';

describe('useUIStore inspector scope/tab machine', () => {
  beforeEach(() => {
    useUIStore.setState({
      inspectorScope: 'auto',
      inspectorTab: 'node',
      checkRequestId: 0,
    });
  });

  it('defaults: auto scope, node tab', () => {
    const s = useUIStore.getState();
    expect(s.inspectorScope).toBe('auto');
    expect(s.inspectorTab).toBe('node');
  });

  it('requestInspectorTab sets BOTH scope and tab in one shot', () => {
    useUIStore.getState().requestInspectorTab('workflow', 'run');
    expect(useUIStore.getState().inspectorScope).toBe('workflow');
    expect(useUIStore.getState().inspectorTab).toBe('run');

    useUIStore.getState().requestInspectorTab('workflow', 'batch');
    expect(useUIStore.getState().inspectorScope).toBe('workflow');
    expect(useUIStore.getState().inspectorTab).toBe('batch');

    // Selecting a node → back to auto (node-scope tabs).
    useUIStore.getState().requestInspectorTab('auto', 'node');
    expect(useUIStore.getState().inspectorScope).toBe('auto');
    expect(useUIStore.getState().inspectorTab).toBe('node');
  });

  it('requestCheck bumps a monotonic counter so repeated requests re-fire', () => {
    expect(useUIStore.getState().checkRequestId).toBe(0);
    useUIStore.getState().requestCheck();
    expect(useUIStore.getState().checkRequestId).toBe(1);
    useUIStore.getState().requestCheck();
    expect(useUIStore.getState().checkRequestId).toBe(2);
  });
});
