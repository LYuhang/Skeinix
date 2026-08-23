/**
 * Command palette action registry.
 *
 * Each entry is a self-contained command surfaced by the Cmd+K palette.
 * Two flavours of handler exist:
 *
 *   1. Navigation — uses the live {@link NavigateFunction} from
 *      `react-router` (passed via `ctx` so we don't capture a stale
 *      reference at module load).
 *   2. Toolbar dispatch — clicks an existing `[data-action="..."]`
 *      button via `document.querySelector(...).click()`. We deliberately
 *      forward to the same DOM affordances the user could reach with the
 *      mouse, so behaviour stays in lockstep (disabled state, busy
 *      state, etc.) without duplicating logic in the palette. See the
 *      `data-action` attributes wired by `CanvasToolbar` and friends.
 *
 * The shortcut strings (e.g. "⌘S") are display-only — actual key
 * handling lives in `KeyboardShortcuts.tsx`. On non-mac platforms users
 * will press Ctrl instead; T18 can localise these labels.
 */
import type { NavigateFunction } from 'react-router';
import { useWorkflowEditStore } from '@/stores/workflow-edit';
import { useUIStore } from '@/stores/ui';
import {
  readSelectedNodeId,
  readSelectedEdge,
} from '@/app/keyboard-shortcut-selection';

export interface ActionCtx {
  navigate: NavigateFunction;
  wfId: string | null;
}

export interface Action {
  id: string;
  labelKey: string;
  shortcut?: string;
  group: 'navigate' | 'workflow' | 'view';
  handler: (ctx: ActionCtx) => void;
}

/** Click the toolbar button (if any) carrying `data-action="<name>"`. */
function clickToolbarAction(name: string): void {
  document
    .querySelector<HTMLButtonElement>(`[data-action="${name}"]`)
    ?.click();
}

/**
 * Flow-coord anchor for a palette paste (no cursor): the selected node's
 * stored position offset by (+24,+24), else the flow origin offset. Mirrors
 * the keyboard handler's fallback (the live viewport center needs the
 * context-bound `screenToFlowPosition`, unavailable here).
 */
function pasteAnchor(): { x: number; y: number } {
  const id = readSelectedNodeId();
  const draft = useWorkflowEditStore.getState().draft;
  if (id && draft) {
    const node = draft[id];
    if (node && typeof node === 'object') {
      const attrs = (node as Record<string, unknown>).__attributes__ as
        | Record<string, unknown>
        | undefined;
      const x = typeof attrs?.x === 'number' ? attrs.x : 0;
      const y = typeof attrs?.y === 'number' ? attrs.y : 0;
      return { x: x + 24, y: y + 24 };
    }
  }
  return { x: 24, y: 24 };
}

export const ACTIONS: Action[] = [
  {
    id: 'goto-workspace',
    labelKey: 'commandPalette.action.gotoWorkspace',
    group: 'navigate',
    handler: ({ navigate }) => navigate('/workspace'),
  },
  {
    id: 'goto-tasks',
    labelKey: 'commandPalette.action.gotoTasks',
    group: 'navigate',
    handler: ({ navigate }) => navigate('/tasks'),
  },
  {
    id: 'goto-deployments',
    labelKey: 'commandPalette.action.gotoDeployments',
    group: 'navigate',
    handler: ({ navigate }) => navigate('/deployments'),
  },
  {
    id: 'goto-settings',
    labelKey: 'commandPalette.action.gotoSettings',
    group: 'navigate',
    handler: ({ navigate }) => navigate('/settings'),
  },
  {
    id: 'save',
    labelKey: 'commandPalette.action.saveWorkflow',
    shortcut: '⌘S',
    group: 'workflow',
    // The real toolbar affordance is `canvas-save` (scoped so future
    // modals with their own Save buttons can't win the selector race) —
    // the bare `save` it used to target never existed.
    handler: () => clickToolbarAction('canvas-save'),
  },
  {
    id: 'check',
    labelKey: 'commandPalette.action.checkWorkflow',
    group: 'workflow',
    // Check lives in the More menu, which is unmounted until the
    // menu opens, so a `[data-action="check"]` DOM-click would silently no-op.
    // Fire the shared UI-store signal instead — RightInspector opens the
    // dialog on the bump.
    handler: () => useUIStore.getState().requestCheck(),
  },
  {
    id: 'undo',
    labelKey: 'commandPalette.action.undo',
    shortcut: '⌘Z',
    group: 'workflow',
    handler: () => clickToolbarAction('undo'),
  },
  {
    id: 'redo',
    labelKey: 'commandPalette.action.redo',
    shortcut: '⌘⇧Z',
    group: 'workflow',
    handler: () => clickToolbarAction('redo'),
  },
  {
    id: 'copy-node',
    labelKey: 'commandPalette.action.copyNode',
    shortcut: '⌘C',
    group: 'workflow',
    // Clipboard/delete commands call the store DIRECTLY — unlike save /
    // undo, there is no toolbar button to forward to. They read the live
    // canvas selection from the DOM (same source as the keyboard handler).
    handler: () => {
      const id = readSelectedNodeId();
      if (id) useWorkflowEditStore.getState().copyNodes([id]);
    },
  },
  {
    id: 'paste-node',
    labelKey: 'commandPalette.action.pasteNode',
    shortcut: '⌘V',
    group: 'workflow',
    handler: () => useWorkflowEditStore.getState().pasteNodes(pasteAnchor()),
  },
  {
    id: 'duplicate-node',
    labelKey: 'commandPalette.action.duplicateNode',
    shortcut: '⌘D',
    group: 'workflow',
    handler: () => {
      const id = readSelectedNodeId();
      if (!id) return;
      const store = useWorkflowEditStore.getState();
      store.copyNodes([id]);
      store.pasteNodes(pasteAnchor());
    },
  },
  {
    id: 'delete-selection',
    labelKey: 'commandPalette.action.deleteSelection',
    shortcut: '⌫',
    group: 'workflow',
    handler: () => {
      const store = useWorkflowEditStore.getState();
      const id = readSelectedNodeId();
      if (id) {
        store.removeNode(id);
        return;
      }
      const edge = readSelectedEdge();
      if (edge) store.disconnectNodes(edge.source, edge.target);
    },
  },
  {
    id: 'execute',
    labelKey: 'commandPalette.action.executeWorkflow',
    group: 'workflow',
    handler: () => clickToolbarAction('execute'),
  },
  {
    id: 'cancel-exec',
    labelKey: 'commandPalette.action.cancelExecution',
    group: 'workflow',
    handler: () => clickToolbarAction('cancel'),
  },
  {
    id: 'back-to-workspace',
    labelKey: 'commandPalette.action.backToWorkspace',
    group: 'navigate',
    handler: () => clickToolbarAction('back'),
  },
];
