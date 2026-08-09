import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { createInstance } from 'i18next';

const i18n = createInstance();
i18n.use(initReactI18next).init({
  lng: 'en',
  resources: {
    en: {
      translation: {
        'nodes_palette.filter': 'Filter nodes…',
        'nodes_palette.no_match': 'No nodes match your filter.',
        'nodes_palette.desc.CodeNode': 'Runs Python code to transform data.',
      },
    },
  },
});

// isolate:false — the shared module registry means ANY file's factory can win
// the slot for ALL consumers in a run. So every file installs the SAME
// delegating factory that reads per-test behavior from globalThis; each test
// sets the delegate it needs in beforeEach. This keeps the mock behaviorally
// identical regardless of which file's factory wins ([[feedback_vitest_isolate_false]]).
const addNode = vi.fn();
vi.mock('@/stores/workflow-edit', () => ({

  useWorkflowEditStore: Object.assign((sel: (s: { addNode: () => void }) => unknown) => sel({ addNode: (globalThis as any).__mockAddNode ?? (() => {}) }), {

    getState: () => ({ addNode: (globalThis as any).__mockAddNode ?? (() => {}) }),
  }),
}));

vi.mock('@/pages/canvas/CanvasViewportContext', () => ({
  useCanvasViewport: () =>

    ((globalThis as any).__mockUseCanvasViewport ?? (() => ({ viewportCenterFlowPos: () => ({ x: 42, y: 7 }) })))(),
}));

import { NodesSection } from '@/pages/canvas/explorer/NodesSection';

const wrap = (ui: React.ReactNode) => render(<I18nextProvider i18n={i18n}>{ui}</I18nextProvider>);

beforeEach(() => {
  addNode.mockClear();

  (globalThis as any).__mockAddNode = addNode;

  (globalThis as any).__mockUseCanvasViewport = () => ({ viewportCenterFlowPos: () => ({ x: 42, y: 7 }) });
});

describe('NodesSection', () => {
  it('lists base node-type cards from NODE_LABELS', () => {
    wrap(<NodesSection readOnly={false} />);
    // canonical types are present
    expect(screen.getByText('Start')).toBeInTheDocument();
    expect(screen.getByText('Code')).toBeInTheDocument();
    expect(screen.getByText('Prompt')).toBeInTheDocument();
  });

  it('double-click inserts at viewport center via addNode', () => {
    wrap(<NodesSection readOnly={false} />);
    const card = screen.getByText('Code').closest('[data-node-card]') as HTMLElement;
    fireEvent.doubleClick(card);
    expect(addNode).toHaveBeenCalledWith(
      expect.objectContaining({ node_type: 'CodeNode', children: [] }),
      { x: 42, y: 7 },
    );
  });

  it('dragstart sets the vibecanvas-node MIME payload', () => {
    wrap(<NodesSection readOnly={false} />);
    const card = screen.getByText('Prompt').closest('[data-node-card]') as HTMLElement;
    const setData = vi.fn();
    fireEvent.dragStart(card, { dataTransfer: { setData, effectAllowed: '' } });
    expect(setData).toHaveBeenCalledWith('application/vibecanvas-node', expect.stringContaining('PromptNode'));
  });

  it('readOnly disables drag + double-click insertion', () => {
    wrap(<NodesSection readOnly />);
    const card = screen.getByText('Code').closest('[data-node-card]') as HTMLElement;
    expect(card.getAttribute('draggable')).toBe('false');
    fireEvent.doubleClick(card);
    expect(addNode).not.toHaveBeenCalled();
  });

  it('filters by type/label/description', () => {
    wrap(<NodesSection readOnly={false} />);
    fireEvent.change(screen.getByPlaceholderText(/filter nodes/i), { target: { value: 'code' } });
    expect(screen.getByText('Code')).toBeInTheDocument();
    expect(screen.queryByText('Prompt')).toBeNull();
  });

  it('shows the no-match empty state', () => {
    wrap(<NodesSection readOnly={false} />);
    fireEvent.change(screen.getByPlaceholderText(/filter nodes/i), { target: { value: 'zzzznope' } });
    expect(screen.getByText('No nodes match your filter.')).toBeInTheDocument();
  });
});
