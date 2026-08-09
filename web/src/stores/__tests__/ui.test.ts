import { beforeEach, describe, expect, it } from 'vitest';
import { useUIStore } from '@/stores/ui';

describe('useUIStore explorer slice', () => {
  beforeEach(() => {
    useUIStore.setState({ explorerOpen: false });
  });

  it('defaults to a collapsed explorer', () => {
    const s = useUIStore.getState();
    expect(s.explorerOpen).toBe(false);
  });

  it('toggleExplorer flips explorerOpen; setExplorerOpen sets it', () => {
    useUIStore.getState().toggleExplorer();
    expect(useUIStore.getState().explorerOpen).toBe(true);
    useUIStore.getState().setExplorerOpen(false);
    expect(useUIStore.getState().explorerOpen).toBe(false);
  });
});

describe('useUIStore per-chat workbench state', () => {
  beforeEach(() => useUIStore.setState({ chatViewStates: {} }));

  it('keeps View, Debug, Sandbox, Todo and focused artifact isolated by chat', () => {
    const store = useUIStore.getState();
    store.setChatViewState('chat:a', {
      previewOpen: true,
      debugOpen: true,
      explorerOpen: true,
      todoCollapsed: true,
      activePreviewId: 'file:a',
    });
    store.setChatViewState('chat:b', { previewOpen: false, todoCollapsed: false });

    expect(useUIStore.getState().chatViewStates['chat:a']).toMatchObject({
      previewOpen: true,
      debugOpen: true,
      explorerOpen: true,
      todoCollapsed: true,
      activePreviewId: 'file:a',
    });
    expect(useUIStore.getState().chatViewStates['chat:b']).toMatchObject({
      previewOpen: false,
      debugOpen: false,
      explorerOpen: false,
      todoCollapsed: false,
    });
  });

  it('composes functional updates without resetting sibling state', () => {
    const store = useUIStore.getState();
    store.setChatViewState('chat:a', { previewOpen: true, todoCollapsed: true });
    store.setChatViewState('chat:a', (current) => ({ debugOpen: !current.debugOpen }));

    expect(useUIStore.getState().chatViewStates['chat:a']).toMatchObject({
      previewOpen: true,
      debugOpen: true,
      todoCollapsed: true,
    });
  });
});
