import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

import i18n from '@/lib/i18n';
import type {
  DiagramSceneV1,
  PreviewDescriptorV1,
} from '@/lib/preview/protocol';
import { DiagramPreviewRenderer } from '../DiagramPreviewRenderer';

const previewApi = vi.hoisted(() => ({
  updateActiveDiagramView: vi.fn().mockResolvedValue(undefined),
  exportPreviewDiagram: vi.fn().mockResolvedValue({
    blob: new Blob(['svg']),
    filename: 'diagram.svg',
  }),
}));
const themeState = vi.hoisted(() => ({ resolvedTheme: 'light' }));

const originalResizeObserver = globalThis.ResizeObserver;
const originalDOMMatrixReadOnly = window.DOMMatrixReadOnly;
const originalOffsetWidth = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  'offsetWidth',
);
const originalOffsetHeight = Object.getOwnPropertyDescriptor(
  HTMLElement.prototype,
  'offsetHeight',
);

class LayoutResizeObserver implements ResizeObserver {
  private readonly callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(target: Element): void {
    queueMicrotask(() => this.callback(
      [{ target } as ResizeObserverEntry],
      this,
    ));
  }

  unobserve(): void {}

  disconnect(): void {}
}

beforeAll(() => {
  globalThis.ResizeObserver = LayoutResizeObserver;
  window.DOMMatrixReadOnly = class {
    readonly m22 = 1;
  } as unknown as typeof DOMMatrixReadOnly;
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', {
    configurable: true,
    get() { return Number.parseFloat(this.style.width) || 900; },
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', {
    configurable: true,
    get() { return Number.parseFloat(this.style.height) || 600; },
  });
});

afterAll(() => {
  globalThis.ResizeObserver = originalResizeObserver;
  window.DOMMatrixReadOnly = originalDOMMatrixReadOnly;
  if (originalOffsetWidth) {
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', originalOffsetWidth);
  }
  if (originalOffsetHeight) {
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', originalOffsetHeight);
  }
});

vi.mock('next-themes', () => ({
  useTheme: () => themeState,
}));

vi.mock('@/lib/api/previews', async (importOriginal) => ({
  ...await importOriginal<typeof import('@/lib/api/previews')>(),
  updateActiveDiagramView: previewApi.updateActiveDiagramView,
  exportPreviewDiagram: previewApi.exportPreviewDiagram,
}));

const scene: DiagramSceneV1 = {
  schemaVersion: 1,
  diagramId: 'request-flow',
  title: 'Request flow',
  family: 'flow',
  diagramType: 'basic',
  compilerVersion: '1.2.0',
  themeVersion: '1.0.0',
  bounds: { x: 0, y: 0, width: 640, height: 360 },
  nodes: [
    {
      id: 'start',
      kind: 'start',
      label: 'Start request',
      labelLines: ['Start request'],
      description: 'Receives input',
      descriptionLines: ['Receives input'],
      styleRole: 'primary',
      importance: 'primary',
      ports: [],
      bounds: { x: 48, y: 120, width: 168, height: 70 },
      sourcePointer: '/model/nodes/0',
      metadata: {},
    },
    {
      id: 'done',
      kind: 'end',
      label: 'Done',
      labelLines: ['Done'],
      descriptionLines: [],
      styleRole: 'success',
      importance: 'primary',
      ports: [],
      bounds: { x: 400, y: 120, width: 168, height: 54 },
      sourcePointer: '/model/nodes/1',
      metadata: {},
    },
  ],
  edges: [
    {
      id: 'start-done',
      source: 'start',
      target: 'done',
      kind: 'flow',
      label: 'Complete',
      importance: 'primary',
      points: [
        { x: 216, y: 155 },
        { x: 400, y: 147 },
      ],
      sourcePointer: '/model/edges/0',
    },
  ],
  groups: [],
  issues: [],
};

function descriptor(
  revision: string,
  diagramScene: DiagramSceneV1 | null,
): PreviewDescriptorV1 {
  return {
    schemaVersion: 1,
    fileRef: {
      schemaVersion: 1,
      scope: 'chat',
      chatId: 'chat-1',
      path: '/data/diagrams/request.vdiagram.json',
    },
    name: 'request.vdiagram.json',
    sizeBytes: 1024,
    contentType: 'application/vnd.vibecanvas.diagram+json',
    detectedType: 'diagram',
    revision,
    renderer: 'diagram',
    loadPolicy: 'inline',
    capabilities: { preview: true, edit: false, download: true },
    diagram: {
      status: diagramScene ? 'valid' : 'invalid',
      scene: diagramScene,
      sourceHash: diagramScene ? 'sha256:source' : undefined,
      issues: diagramScene ? [] : [{
        severity: 'error',
        stage: 'schema',
        code: 'invalid_json',
        json_pointer: '/',
        message: 'Invalid JSON',
      }],
    },
  };
}

afterEach(async () => {
  sessionStorage.clear();
  previewApi.updateActiveDiagramView.mockClear();
  previewApi.exportPreviewDiagram.mockClear();
  themeState.resolvedTheme = 'light';
  await i18n.changeLanguage('en');
});

describe('DiagramPreviewRenderer', () => {
  it('materializes compiled scene edges through invisible connectivity handles', async () => {
    render(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={descriptor('sha256:edges', scene)}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );

    await waitFor(() => {
      expect(document.querySelectorAll('.react-flow__handle-left')).toHaveLength(2);
      expect(document.querySelectorAll('.react-flow__handle-right')).toHaveLength(2);
      expect(document.querySelectorAll('.react-flow__edge-path')).toHaveLength(1);
    });
  });

  it('uses the shared dark palette when Preview is dark', () => {
    themeState.resolvedTheme = 'dark';
    render(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={descriptor('sha256:dark', scene)}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );

    expect(screen.getByRole('region', { name: 'Request flow diagram' }))
      .toHaveAttribute('data-diagram-theme', 'dark');
    expect(document.querySelector('[data-diagram-element-id="start"]'))
      .toHaveStyle({ backgroundColor: '#29283a', borderColor: '#555d68' });
  });

  it('exports a stable Light artifact even when Preview is dark', async () => {
    themeState.resolvedTheme = 'dark';
    const user = userEvent.setup();
    render(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={descriptor('sha256:dark-export', scene)}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );

    for (const [label, format] of [
      ['SVG', 'svg'],
      ['PNG', 'png'],
      ['PDF', 'pdf'],
    ] as const) {
      await user.click(screen.getByRole('button', { name: 'Export' }));
      await user.click(await screen.findByRole('menuitem', { name: new RegExp(`^${label}`) }));
      await waitFor(() => expect(previewApi.exportPreviewDiagram).toHaveBeenCalledWith(
        expect.objectContaining({
          expectedRevision: 'sha256:dark-export',
          format,
          theme: 'light',
          background: 'white',
        }),
      ));
    }
  });

  it('keeps the last valid Scene when a newer revision is invalid', () => {
    const { rerender } = render(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={descriptor('sha256:valid', scene)}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );
    expect(screen.getAllByText('Request flow').length).toBeGreaterThan(0);

    rerender(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={descriptor('sha256:invalid', null)}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );

    expect(screen.getAllByText('Request flow').length).toBeGreaterThan(0);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Showing the last successfully compiled diagram',
    );

    const unrelatedInvalid = descriptor('sha256:other-invalid', null);
    unrelatedInvalid.fileRef.path = '/data/diagrams/other.vdiagram.json';
    rerender(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={unrelatedInvalid}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );
    expect(screen.queryByText('Request flow')).not.toBeInTheDocument();
    expect(screen.getByText('Diagram source needs attention')).toBeInTheDocument();
  });

  it('searches without moving focus on every keystroke', () => {
    render(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={descriptor('sha256:valid', scene)}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );

    fireEvent.change(screen.getByRole('textbox', { name: 'Find a node' }), {
      target: { value: 'done' },
    });
    expect(screen.getAllByText('Done').length).toBeGreaterThan(0);
  });

  it('syncs a selected element for the exact Preview revision', async () => {
    render(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={descriptor('sha256:valid', scene)}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );

    fireEvent.click(screen.getByText('Start request'));

    await waitFor(() => expect(previewApi.updateActiveDiagramView).toHaveBeenCalledWith(
      expect.objectContaining({
        chatId: 'chat-1',
        path: '/data/diagrams/request.vdiagram.json',
        revision: 'sha256:valid',
        sourceHash: 'sha256:source',
        selectedElementIds: ['start'],
      }),
    ));
  });

  it('restores a stable selected node when live Preview remounts', async () => {
    const first = render(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={descriptor('sha256:before', scene)}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );

    fireEvent.click(screen.getByText('Start request'));
    await waitFor(() => expect(
      document.querySelector('[data-diagram-element-id="start"]'),
    ).toHaveClass('ring-2'));
    first.unmount();

    render(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={descriptor('sha256:after', {
            ...scene,
            title: 'Updated request flow',
          })}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );

    await waitFor(() => expect(
      document.querySelector('[data-diagram-element-id="start"]'),
    ).toHaveClass('ring-2'));
  });

  it('treats live draft revisions as read-only, non-exportable context', async () => {
    const draft = descriptor('draft-revision-2', {
      ...scene,
      edges: scene.edges.map((edge) => ({
        ...edge,
        crossings: [{ x: 300, y: 151, style: 'gap', overEdgeId: 'other' }],
      })),
    });
    draft.capabilities.download = false;
    draft.diagram!.draft = {
      draftId: 'draft-1',
      status: 'ready',
      sequence: 2,
      terminal: false,
      operation: 'add_process',
      elementIds: ['start', 'start-done'],
    };

    render(
      <div style={{ width: 900, height: 600 }}>
        <DiagramPreviewRenderer
          descriptor={draft}
          loadAllowed
          onDirtyChange={() => undefined}
        />
      </div>,
    );

    expect(screen.getByText(/Draft · revision 2/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Export' })).not.toBeInTheDocument();
    await waitFor(() => {
      expect(document.querySelectorAll('[data-diagram-crossing="gap"]')).toHaveLength(1);
    });
    // React Flow may finish a prior test's deferred viewport callback after
    // cleanup. This assertion is scoped to interaction with the draft itself.
    previewApi.updateActiveDiagramView.mockClear();
    fireEvent.click(screen.getByText('Start request'));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    expect(previewApi.updateActiveDiagramView).not.toHaveBeenCalledWith(
      expect.objectContaining({ revision: 'draft-revision-2' }),
    );
  });
});
