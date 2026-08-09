import {
  Component,
  memo,
  useCallback,
  useDeferredValue,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  Background,
  BaseEdge,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
  type NodeTypes,
  type Viewport,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  AlertTriangle,
  Box,
  Braces,
  Database,
  Download,
  Focus,
  Maximize2,
  Minimize2,
  Minus,
  Network,
  Plus,
  Search,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useTheme } from 'next-themes';

import { AsyncState } from '@/components/ui/async-state';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  PreviewApiError,
  exportPreviewDiagram,
  updateActiveDiagramView,
  type DiagramViewportBounds,
} from '@/lib/api/previews';
import { cn } from '@/lib/utils';
import type { DiagramIssueV1, DiagramSceneV1 } from '@/lib/preview/protocol';
import { recordDiagramPreviewTimeline } from '@/lib/preview/diagram-timeline';
import visualTokens from '@/lib/preview/diagram-visual-tokens.json';
import type { PreviewRendererProps } from './renderer-types';

type DiagramTheme = 'light' | 'dark' | 'print';
type DiagramPalette = (typeof visualTokens)['light'];

interface DiagramNodeData extends Record<string, unknown> {
  sceneNode: DiagramSceneV1['nodes'][number];
  palette: DiagramPalette;
  changed: boolean;
}

interface DiagramGroupData extends Record<string, unknown> {
  group: DiagramSceneV1['groups'][number];
  palette: DiagramPalette;
}

interface DiagramEdgeData extends Record<string, unknown> {
  sceneEdge: DiagramSceneV1['edges'][number];
  palette: DiagramPalette;
  changed: boolean;
}

function NodeKindIcon({
  kind,
  assetRef,
}: {
  kind: string;
  assetRef?: string | null;
}) {
  if (assetRef === 'platform.database' || ['database', 'storage'].includes(kind)) {
    return <Database className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  if (assetRef === 'platform.cloud' || ['system', 'network', 'cloud'].includes(kind)) {
    return <Network className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  if (['service', 'application', 'component'].includes(kind)) {
    return <Box className="h-3.5 w-3.5" aria-hidden="true" />;
  }
  return <Braces className="h-3.5 w-3.5" aria-hidden="true" />;
}

const DiagramNode = memo(function DiagramNode({ data, selected }: NodeProps<Node<DiagramNodeData>>) {
  const value = data.sceneNode;
  const palette = data.palette;
  const roleFills = palette.roleFills as Record<string, string>;
  return (
    <div
      className={cn(
        'relative h-full w-full overflow-hidden rounded-lg border text-left shadow-sm transition-[border-color,box-shadow] duration-feedback',
        selected && 'border-focus shadow-md ring-2 ring-focus/20',
        data.changed && 'ring-2 ring-focus/35',
      )}
      style={{
        backgroundColor: roleFills[value.styleRole] ?? roleFills.neutral,
        borderColor: selected ? undefined : palette.border,
        color: palette.foreground,
      }}
      data-diagram-element-id={value.id}
      role="group"
      aria-label={`${value.label}, ${value.kind}`}
    >
      {/* React Flow only materializes edges when custom nodes expose handles.
          The compiled Scene owns the exact edge geometry, so these anchors are
          intentionally invisible and exist only to establish connectivity. */}
      <Handle
        type="target"
        position={Position.Left}
        isConnectable={false}
        className="!pointer-events-none !h-px !w-px !border-0 !bg-transparent !opacity-0"
      />
      <Handle
        type="source"
        position={Position.Right}
        isConnectable={false}
        className="!pointer-events-none !h-px !w-px !border-0 !bg-transparent !opacity-0"
      />
      <div className="flex h-full min-h-0 flex-col px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span
            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md"
            style={{ color: palette.secondary, backgroundColor: `${palette.foreground}0f` }}
          >
            <NodeKindIcon kind={value.kind} assetRef={value.assetRef} />
          </span>
          <span className="min-w-0 flex-1 text-sm font-semibold leading-5" style={{ color: palette.foreground }}>
            {value.labelLines.map((line, index) => (
              <span key={`${index}:${line}`} className="block">{line}</span>
            ))}
          </span>
        </div>
        {value.descriptionLines.length ? (
          <p className="mt-1.5 text-xs leading-4" style={{ color: palette.secondary }}>
            {value.descriptionLines.map((line, index) => (
              <span key={`${index}:${line}`} className="block">{line}</span>
            ))}
          </p>
        ) : null}
        {value.ports.map((port) => (
          <span
            key={port.id}
            title={port.label ?? port.id}
            className="absolute h-2 w-2 rounded-full"
            style={{
              left: port.x - value.bounds.x - 4,
              top: port.y - value.bounds.y - 4,
              backgroundColor: palette.foreground,
              boxShadow: `0 0 0 2px ${roleFills[value.styleRole] ?? roleFills.neutral}`,
            }}
            aria-hidden="true"
          />
        ))}
      </div>
    </div>
  );
});

const DiagramGroup = memo(function DiagramGroup({ data }: NodeProps<Node<DiagramGroupData>>) {
  const { palette } = data;
  return (
    <div
      className="h-full w-full rounded-xl border border-dashed"
      style={{ borderColor: palette.border, backgroundColor: `${palette.background}8f` }}
      role="group"
      aria-label={data.group.label}
    >
      <div className="px-3 pt-2 text-xs font-semibold uppercase tracking-wide" style={{ color: palette.secondary }}>
        {data.group.label}
      </div>
    </div>
  );
});

const SceneEdge = memo(function SceneEdge({ data, markerEnd, selected }: EdgeProps<Edge<DiagramEdgeData>>) {
  const value = data?.sceneEdge;
  if (!value || value.points.length < 2) return null;
  const palette = data.palette;
  const path = value.points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');
  const middle = value.points[Math.floor(value.points.length / 2)];
  return (
    <>
      <BaseEdge
        path={path}
        markerEnd={markerEnd}
        style={{
          stroke: palette.edge,
          strokeWidth: selected || data.changed ? 2.5 : value.importance === 'primary' ? 2 : 1.5,
        }}
      />
      {value.crossings?.map((crossing, index) => (
        <circle
          key={`${crossing.x}:${crossing.y}:${index}`}
          cx={crossing.x}
          cy={crossing.y}
          r={4}
          fill={palette.background}
          stroke="none"
          data-diagram-crossing="gap"
          data-diagram-edge-id={value.id}
          aria-hidden="true"
        />
      ))}
      {value.label && middle ? (
        <foreignObject
          x={middle.x - 70}
          y={middle.y - 13}
          width={140}
          height={26}
          className="pointer-events-none overflow-visible"
        >
          <div
            className="mx-auto w-fit max-w-[136px] truncate rounded border px-1.5 py-0.5 text-xs shadow-sm"
            style={{
              borderColor: palette.border,
              backgroundColor: palette.background,
              color: palette.secondary,
            }}
          >
            {value.label}
          </div>
        </foreignObject>
      ) : null}
    </>
  );
});

const NODE_TYPES: NodeTypes = {
  diagramNode: DiagramNode as NodeTypes[string],
  diagramGroup: DiagramGroup as NodeTypes[string],
};
const EDGE_TYPES = { sceneEdge: SceneEdge };

function storageKey(descriptor: PreviewRendererProps['descriptor']) {
  const ref = descriptor.fileRef;
  return `vibecanvas:diagram-viewport:${ref.scope}:${'chatId' in ref ? ref.chatId : 'runId' in ref ? ref.runId : ''}:${ref.path}`;
}

function DiagramCanvas({
  descriptor,
  scene,
  showingPreviousRevision,
}: Pick<PreviewRendererProps, 'descriptor'> & {
  scene: DiagramSceneV1 | null;
  showingPreviousRevision: boolean;
}) {
  const { t } = useTranslation();
  const { resolvedTheme } = useTheme();
  const theme: DiagramTheme = resolvedTheme === 'dark' ? 'dark' : 'light';
  const palette = visualTokens[theme];
  const payload = descriptor.diagram;
  const flow = useReactFlow();
  const surfaceRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const contextSyncSequence = useRef(0);
  const viewportKey = useMemo(() => storageKey(descriptor), [descriptor]);
  const selectionKey = `${viewportKey}:selected-element`;
  const savedViewport = useMemo<Viewport | undefined>(() => {
    try {
      const value = sessionStorage.getItem(viewportKey);
      return value ? JSON.parse(value) as Viewport : undefined;
    } catch {
      return undefined;
    }
  }, [viewportKey]);
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase());
  const [selection, setSelection] = useState<{
    key: string;
    elementId: string | null;
  }>(() => {
    try {
      return { key: selectionKey, elementId: sessionStorage.getItem(selectionKey) };
    } catch {
      return { key: selectionKey, elementId: null };
    }
  });
  const [problemsOpen, setProblemsOpen] = useState(false);
  const [minimapOpen, setMinimapOpen] = useState(() => (
    window.matchMedia('(min-width: 768px)').matches
  ));
  const [fullscreen, setFullscreen] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [contextSyncError, setContextSyncError] = useState<string | null>(null);
  const [highlightedIds, setHighlightedIds] = useState<Set<string>>(new Set());
  const [prefersReducedMotion] = useState(() => window.matchMedia(
    '(prefers-reduced-motion: reduce)',
  ).matches);

  let selectionCandidate = selection.elementId;
  if (selection.key !== selectionKey) {
    try {
      selectionCandidate = sessionStorage.getItem(selectionKey);
    } catch {
      selectionCandidate = null;
    }
  }
  const selectedId = selectionCandidate
    && scene?.nodes.some((node) => node.id === selectionCandidate)
    ? selectionCandidate
    : null;
  const [zoom, setZoom] = useState(savedViewport?.zoom ?? 1);
  const draftState = payload?.draft;
  const draftElementKey = (draftState?.elementIds ?? []).join('\u0000');

  useEffect(() => {
    const ids = draftState?.elementIds ?? [];
    const startTimer = window.setTimeout(
      () => setHighlightedIds(new Set(prefersReducedMotion ? [] : ids)),
      0,
    );
    const endTimer = window.setTimeout(() => setHighlightedIds(new Set()), 200);
    return () => {
      window.clearTimeout(startTimer);
      window.clearTimeout(endTimer);
    };
    // draftElementKey is the stable scalar identity of the bounded ID list.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftElementKey, draftState?.sequence, prefersReducedMotion]);

  const nodes = useMemo<Node[]>(() => {
    if (!scene) return [];
    const groupNodes: Node<DiagramGroupData>[] = scene.groups.map((group) => ({
      id: `group:${group.id}`,
      type: 'diagramGroup',
      position: { x: group.bounds.x, y: group.bounds.y },
      style: { width: group.bounds.width, height: group.bounds.height, zIndex: -1 },
      measured: { width: group.bounds.width, height: group.bounds.height },
      selectable: false,
      data: { group, palette },
    }));
    const semanticNodes: Node<DiagramNodeData>[] = scene.nodes.map((node) => ({
      id: node.id,
      type: 'diagramNode',
      position: { x: node.bounds.x, y: node.bounds.y },
      style: { width: node.bounds.width, height: node.bounds.height },
      measured: { width: node.bounds.width, height: node.bounds.height },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      selected: selectedId === node.id,
      data: { sceneNode: node, palette, changed: highlightedIds.has(node.id) },
    }));
    return [...groupNodes, ...semanticNodes];
  }, [highlightedIds, palette, scene, selectedId]);
  const edges = useMemo<Edge<DiagramEdgeData>[]>(() => (scene?.edges ?? []).map((edge) => ({
    id: edge.id,
    source: edge.source,
    target: edge.target,
    type: 'sceneEdge',
    markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: palette.edge },
    data: { sceneEdge: edge, palette, changed: highlightedIds.has(edge.id) },
  })), [highlightedIds, palette, scene]);
  const selectedNode = scene?.nodes.find((node) => node.id === selectedId) ?? null;
  const nodeLabelById = useMemo(
    () => new Map((scene?.nodes ?? []).map((node) => [node.id, node.label])),
    [scene],
  );
  const issues = payload?.issues ?? [];
  const searchMatches = useMemo(() => {
    if (!scene || !deferredQuery) return [];
    return scene.nodes.filter((node) => (
      node.label.toLocaleLowerCase().includes(deferredQuery)
      || node.id.toLocaleLowerCase().includes(deferredQuery)
    )).slice(0, 8);
  }, [deferredQuery, scene]);

  const motionDuration = useCallback(
    (duration: number) => (prefersReducedMotion ? 0 : duration),
    [prefersReducedMotion],
  );

  const canvasViewportBounds = useCallback((viewport?: Viewport): DiagramViewportBounds | null => {
    const element = canvasRef.current;
    const current = viewport ?? flow.getViewport();
    if (!element || !Number.isFinite(current.zoom) || current.zoom <= 0) return null;
    const round = (value: number) => Math.round(value * 100) / 100;
    return {
      x: round(-current.x / current.zoom),
      y: round(-current.y / current.zoom),
      width: round(element.clientWidth / current.zoom),
      height: round(element.clientHeight / current.zoom),
    };
  }, [flow]);

  const syncActiveContext = useCallback((
    selectedElementIds: string[],
    viewport?: Viewport,
  ) => {
    const ref = descriptor.fileRef;
    const sourceHash = payload?.sourceHash;
    if (
      ref.scope !== 'chat'
      || !ref.path.startsWith('/data/')
      || !sourceHash
      || payload?.status !== 'valid'
      || payload.draft
    ) return;
    const sequence = ++contextSyncSequence.current;
    void updateActiveDiagramView({
      chatId: ref.chatId,
      path: ref.path as `/data/${string}`,
      revision: descriptor.revision,
      sourceHash,
      selectedElementIds,
      viewportBounds: canvasViewportBounds(viewport),
    }).then(() => {
      if (contextSyncSequence.current === sequence) setContextSyncError(null);
    }).catch((error: unknown) => {
      if (contextSyncSequence.current !== sequence) return;
      // A present event can legitimately supersede an in-flight viewport
      // update. The next move/select sends context for the new revision.
      if (error instanceof PreviewApiError && error.status === 409) {
        setContextSyncError(null);
        return;
      }
      setContextSyncError(t(
        'preview.diagram.contextSyncFailed',
        'Canvas context could not be shared with the agent.',
      ));
    });
  }, [canvasViewportBounds, descriptor.fileRef, descriptor.revision, payload, t]);

  const selectElement = useCallback((elementId: string | null) => {
    setSelection({ key: selectionKey, elementId });
    try {
      if (elementId) sessionStorage.setItem(selectionKey, elementId);
      else sessionStorage.removeItem(selectionKey);
    } catch {
      // Preview remains usable when Web Storage is unavailable.
    }
    syncActiveContext(elementId ? [elementId] : []);
  }, [selectionKey, syncActiveContext]);

  const focusElement = useCallback((elementId: string) => {
    const target = scene?.nodes.find((node) => node.id === elementId);
    if (!target) return;
    selectElement(elementId);
    void flow.setCenter(
      target.bounds.x + target.bounds.width / 2,
      target.bounds.y + target.bounds.height / 2,
      { zoom: 1.1, duration: motionDuration(220) },
    );
  }, [flow, motionDuration, scene, selectElement]);

  const exportDiagram = useCallback(async (format: 'svg' | 'png' | 'pdf') => {
    setExporting(true);
    setExportError(null);
    try {
      const result = await exportPreviewDiagram({
        fileRef: descriptor.fileRef,
        expectedRevision: descriptor.revision,
        format,
        // Preview follows the application theme, while downloaded artifacts
        // deliberately use one stable, shareable visual contract.
        theme: 'light',
        background: 'white',
      });
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setExportError(error instanceof Error
        ? error.message
        : t('preview.diagram.exportFailed', 'Diagram export failed.'));
    } finally {
      setExporting(false);
    }
  }, [descriptor.fileRef, descriptor.revision, t]);

  useEffect(() => {
    const listener = () => setFullscreen(document.fullscreenElement === surfaceRef.current);
    document.addEventListener('fullscreenchange', listener);
    return () => document.removeEventListener('fullscreenchange', listener);
  }, []);

  useLayoutEffect(() => {
    if (!scene || descriptor.diagram?.scene !== scene) return;
    const path = descriptor.fileRef.path;
    const revision = descriptor.revision;
    recordDiagramPreviewTimeline({
      stage: 'T2', path, revision, timestamp: Date.now(),
    });
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => {
        recordDiagramPreviewTimeline({
          stage: 'T3', path, revision, timestamp: Date.now(),
        });
      });
    });
    return () => {
      cancelAnimationFrame(firstFrame);
      if (secondFrame) cancelAnimationFrame(secondFrame);
    };
  }, [descriptor.diagram?.scene, descriptor.fileRef.path, descriptor.revision, scene]);

  if (!scene) {
    return (
      <div className="h-full overflow-auto p-4">
        <AsyncState
          kind="error"
          title={t('preview.diagram.invalidTitle', 'Diagram source needs attention')}
          description={t(
            'preview.diagram.invalidDescription',
            'The semantic source could not be compiled. Fix the reported JSON locations and refresh Preview.',
          )}
        />
        <ProblemList issues={issues} onSelect={() => undefined} />
      </div>
    );
  }

  return (
    <div
      ref={surfaceRef}
      className="relative flex h-full min-h-0 flex-col bg-surface-work"
      data-role="diagram-preview"
      data-diagram-path={descriptor.fileRef.path}
      data-diagram-revision={descriptor.revision}
      data-diagram-id={scene.diagramId}
      data-diagram-theme={theme}
      data-reduced-motion={prefersReducedMotion ? 'true' : 'false'}
      role="region"
      style={{ backgroundColor: palette.background, color: palette.foreground }}
      aria-label={t('preview.diagram.canvasLabel', '{{title}} diagram', {
        title: scene.title,
      })}
    >
      <div
        className="flex min-h-11 shrink-0 items-center gap-2 overflow-x-auto border-b border-edge-subtle bg-surface-raised/95 px-3"
        role="toolbar"
        aria-label={t('preview.diagram.toolbar', 'Diagram controls')}
      >
        <div className="min-w-0 flex-1">
          <div className="truncate text-xs font-semibold text-content-primary">{scene.title}</div>
          <div className="truncate text-xs text-content-tertiary">
            {scene.family} · {scene.diagramType} · {draftState
              ? t('preview.diagram.draftRevision', 'Draft · revision {{sequence}}', {
                  sequence: draftState.sequence,
                })
              : payload?.status === 'valid'
                ? t('preview.diagram.valid', 'Valid')
                : t('preview.diagram.invalid', 'Invalid')} · {descriptor.revision.slice(0, 15)}
          </div>
        </div>
        <label className="relative w-36 shrink-0 sm:w-44">
          <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-content-tertiary" />
          <input
            ref={searchRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t('preview.diagram.search', 'Find a node')}
            aria-label={t('preview.diagram.search', 'Find a node')}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && searchMatches[0]) {
                focusElement(searchMatches[0].id);
              }
            }}
            className="h-7 w-full rounded-md border border-edge-subtle bg-surface-work pl-7 pr-2 text-xs outline-none focus:border-focus focus:ring-1 focus:ring-focus/25"
          />
          {deferredQuery ? (
            <div className="absolute right-0 top-9 z-40 max-h-64 w-64 overflow-auto rounded-md border border-edge-subtle bg-surface-raised p-1 shadow-lg">
              {searchMatches.length ? searchMatches.map((node) => (
                <button
                  key={node.id}
                  type="button"
                  className="flex min-h-9 w-full items-center justify-between gap-3 rounded px-2 text-left text-xs hover:bg-surface-sunken focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
                  onClick={() => {
                    focusElement(node.id);
                    setQuery('');
                  }}
                >
                  <span className="truncate">{node.label}</span>
                  <code className="shrink-0 text-content-tertiary">{node.id}</code>
                </button>
              )) : (
                <p className="px-2 py-2 text-xs text-content-secondary">
                  {t('preview.diagram.noMatches', 'No matching nodes.')}
                </p>
              )}
            </div>
          ) : null}
        </label>
        <Button
          className="max-sm:h-11"
          variant={problemsOpen ? 'secondary' : 'ghost'}
          size="sm"
          aria-label={t('preview.diagram.problemCount', '{{count}} diagram problems', { count: issues.length })}
          onClick={() => setProblemsOpen((value) => !value)}
        >
          <AlertTriangle className="mr-1 h-3.5 w-3.5" />
          {issues.length}
        </Button>
        {descriptor.capabilities.download ? <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              className="max-sm:h-11"
              variant="ghost"
              size="sm"
              disabled={exporting}
              data-action="diagram-export"
            >
              <Download className="mr-1 h-3.5 w-3.5" />
              {exporting
                ? t('preview.diagram.exporting', 'Exporting…')
                : t('preview.diagram.export', 'Export')}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => void exportDiagram('svg')}>SVG · {t('preview.diagram.scalable', 'scalable')}</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void exportDiagram('png')}>PNG · {t('preview.diagram.image', 'image')}</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => void exportDiagram('pdf')}>PDF · {t('preview.diagram.print', 'print')}</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu> : null}
        <Button data-action="diagram-toggle-minimap" className="max-sm:h-11 max-sm:w-11" variant="ghost" size="icon-sm" aria-label={t('preview.diagram.toggleMinimap', 'Toggle minimap')} onClick={() => setMinimapOpen((value) => !value)}>
          <Network className="h-3.5 w-3.5" />
        </Button>
        <Button className="max-sm:h-11 max-sm:w-11" variant="ghost" size="icon-sm" aria-label={t('preview.diagram.zoomOut', 'Zoom out')} onClick={() => void flow.zoomOut({ duration: motionDuration(150) })}>
          <Minus className="h-3.5 w-3.5" />
        </Button>
        <Button
          className="min-w-12 px-1.5 text-xs tabular-nums max-sm:h-11"
          variant="ghost"
          size="sm"
          aria-label={t('preview.diagram.resetZoom', 'Reset zoom to 100%')}
          onClick={() => void flow.zoomTo(1, { duration: motionDuration(150) })}
        >
          {Math.round(zoom * 100)}%
        </Button>
        <Button className="max-sm:h-11 max-sm:w-11" variant="ghost" size="icon-sm" aria-label={t('preview.diagram.zoomIn', 'Zoom in')} onClick={() => void flow.zoomIn({ duration: motionDuration(150) })}>
          <Plus className="h-3.5 w-3.5" />
        </Button>
        <Button data-action="diagram-fit" className="max-sm:h-11 max-sm:w-11" variant="ghost" size="icon-sm" aria-label={t('preview.diagram.fit', 'Fit diagram')} onClick={() => void flow.fitView({ padding: 0.16, duration: motionDuration(220) })}>
          <Focus className="h-3.5 w-3.5" />
        </Button>
        <Button
          data-action="diagram-fullscreen"
          variant="ghost"
          size="icon-sm"
          className="max-sm:h-11 max-sm:w-11"
          aria-label={fullscreen
            ? t('preview.diagram.exitFullscreen', 'Exit fullscreen')
            : t('preview.diagram.enterFullscreen', 'Enter fullscreen')}
          onClick={() => {
            if (document.fullscreenElement) void document.exitFullscreen();
            else void surfaceRef.current?.requestFullscreen();
          }}
        >
          {fullscreen ? <Minimize2 className="h-3.5 w-3.5" /> : <Maximize2 className="h-3.5 w-3.5" />}
        </Button>
      </div>
      <div
        ref={canvasRef}
        className="relative min-h-0 flex-1"
        role="group"
        aria-label={t('preview.diagram.viewport', 'Diagram viewport')}
      >
        <div className="sr-only" data-role="diagram-accessible-summary">
          <p>{scene.title}</p>
          <ul>
            {scene.nodes.map((node) => (
              <li key={node.id}>{node.label} ({node.kind})</li>
            ))}
          </ul>
          <ul>
            {scene.edges.map((edge) => {
              return (
                <li key={edge.id}>
                  {nodeLabelById.get(edge.source) ?? edge.source}
                  {' → '}
                  {nodeLabelById.get(edge.target) ?? edge.target}
                  {edge.label ? `: ${edge.label}` : ''}
                </li>
              );
            })}
          </ul>
        </div>
        {showingPreviousRevision ? (
          <div role="status" className="absolute left-1/2 top-3 z-30 max-w-md -translate-x-1/2 rounded-md border border-state-warning/30 bg-surface-raised px-3 py-2 text-xs text-content-primary shadow-lg">
            {t(
              'preview.diagram.previousRevision',
              'The latest revision is invalid. Showing the last successfully compiled diagram.',
            )}
          </div>
        ) : null}
        {draftState && draftState.status !== 'ready' && !draftState.terminal ? (
          <div role="status" className="absolute left-1/2 top-3 z-30 max-w-md -translate-x-1/2 rounded-md border border-edge-subtle bg-surface-raised px-3 py-2 text-xs text-content-secondary shadow-lg">
            {draftState.status === 'invalid'
              ? t(
                  'preview.diagramDraft.invalid',
                  'The latest edit is not renderable. Showing the last valid revision while the agent repairs it.',
                )
              : t('preview.diagramDraft.updating', 'Agent is updating the diagram…')}
          </div>
        ) : null}
        {exportError ? (
          <div role="alert" className="absolute left-1/2 top-3 z-30 max-w-md -translate-x-1/2 rounded-md border border-state-danger/30 bg-surface-raised px-3 py-2 text-xs text-state-danger shadow-lg">
            {exportError}
          </div>
        ) : null}
        {contextSyncError ? (
          <div role="status" className="absolute bottom-3 left-3 z-30 max-w-sm rounded-md border border-state-warning/30 bg-surface-raised px-3 py-2 text-xs text-content-secondary shadow-lg">
            {contextSyncError}
          </div>
        ) : null}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          edgeTypes={EDGE_TYPES}
          defaultViewport={savedViewport}
          fitView={!savedViewport}
          fitViewOptions={{ padding: 0.16 }}
          minZoom={0.12}
          maxZoom={2.5}
          nodesDraggable={false}
          nodesConnectable={false}
          onlyRenderVisibleElements={scene.nodes.length > 200}
          elementsSelectable
          panOnScroll
          selectionOnDrag={false}
          onNodeClick={(_, node) => {
            if (!node.id.startsWith('group:')) selectElement(node.id);
          }}
          onPaneClick={() => selectElement(null)}
          onMoveEnd={(_, viewport) => {
            setZoom(viewport.zoom);
            try { sessionStorage.setItem(viewportKey, JSON.stringify(viewport)); } catch { /* storage may be unavailable */ }
            syncActiveContext(
              selectedId && scene.nodes.some((node) => node.id === selectedId)
                ? [selectedId]
                : [],
              viewport,
            );
          }}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={24} size={1} color={palette.border} bgColor={palette.background} />
          {minimapOpen ? (
            <MiniMap
              pannable
              zoomable
              position="bottom-left"
              className="!border !border-edge-subtle !bg-surface-raised/95"
              maskColor={`${palette.background}b8`}
              nodeColor={palette.secondary}
            />
          ) : null}
        </ReactFlow>
        {problemsOpen ? (
          <aside
            className="absolute bottom-3 right-3 top-3 z-20 w-[min(360px,calc(100%-24px))] overflow-auto rounded-lg border border-edge-subtle bg-surface-raised/95 p-3 shadow-lg backdrop-blur"
            aria-label={t('preview.diagram.problems', 'Problems')}
          >
            <div className="mb-2 text-xs font-semibold">{t('preview.diagram.problems', 'Problems')}</div>
            <ProblemList issues={issues} onSelect={focusElement} />
          </aside>
        ) : selectedNode ? (
          <aside
            className="absolute right-3 top-3 z-20 w-[min(320px,calc(100%-24px))] rounded-lg border border-edge-subtle bg-surface-raised/95 p-3 shadow-lg backdrop-blur"
            aria-label={t('preview.diagram.inspector', 'Diagram inspector')}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold">{selectedNode.label}</div>
                <div className="mt-0.5 text-xs uppercase tracking-wide text-content-tertiary">{selectedNode.kind}</div>
              </div>
              <Button variant="ghost" size="icon-sm" aria-label={t('preview.diagram.closeInspector', 'Close inspector')} onClick={() => selectElement(null)}>×</Button>
            </div>
            {selectedNode.description ? <p className="mt-3 text-xs leading-5 text-content-secondary">{selectedNode.description}</p> : null}
            <dl className="mt-3 grid grid-cols-[72px_1fr] gap-x-2 gap-y-2 border-t border-edge-subtle pt-3 text-xs">
              <dt className="text-content-tertiary">ID</dt><dd className="break-all font-mono">{selectedNode.id}</dd>
              <dt className="text-content-tertiary">{t('preview.diagram.source', 'Source')}</dt><dd className="break-all font-mono">{selectedNode.sourcePointer}</dd>
              <dt className="text-content-tertiary">{t('preview.diagram.incoming', 'Incoming')}</dt>
              <dd className="space-y-1">{scene.edges.filter((edge) => edge.target === selectedNode.id).map((edge) => (
                <button key={edge.id} type="button" className="block break-all text-left font-mono text-focus hover:underline" onClick={() => focusElement(edge.source)}>{edge.source} → {edge.id}</button>
              ))}</dd>
              <dt className="text-content-tertiary">{t('preview.diagram.outgoing', 'Outgoing')}</dt>
              <dd className="space-y-1">{scene.edges.filter((edge) => edge.source === selectedNode.id).map((edge) => (
                <button key={edge.id} type="button" className="block break-all text-left font-mono text-focus hover:underline" onClick={() => focusElement(edge.target)}>{edge.id} → {edge.target}</button>
              ))}</dd>
            </dl>
          </aside>
        ) : null}
      </div>
    </div>
  );
}

function ProblemList({ issues, onSelect }: { issues: DiagramIssueV1[]; onSelect: (id: string) => void }) {
  const { t } = useTranslation();
  if (issues.length === 0) {
    return <p className="text-xs text-content-secondary">{t('preview.diagram.noProblems', 'No structural or visual issues.')}</p>;
  }
  return (
    <div className="space-y-2">
      {issues.map((issue, index) => (
        <button
          type="button"
          key={`${issue.code}:${issue.json_pointer}:${index}`}
          className="w-full rounded-md border border-edge-subtle p-2 text-left hover:border-edge-strong hover:bg-surface-sunken"
          onClick={() => issue.element_id && onSelect(issue.element_id)}
          disabled={!issue.element_id}
        >
          <div className="flex items-center gap-2 text-xs font-medium">
            <span className={cn(
              'h-1.5 w-1.5 rounded-full',
              issue.disposition === 'accepted' || issue.disposition === 'render_cue'
                ? 'bg-content-tertiary'
                : issue.severity === 'error' || issue.disposition === 'blocking'
                  ? 'bg-state-danger'
                  : 'bg-state-warning',
            )} />
            {issue.code.replaceAll('_', ' ')}
            {issue.disposition ? (
              <span className="ml-auto text-xs font-normal text-content-tertiary">
                {issue.disposition.replaceAll('_', ' ')}
              </span>
            ) : null}
          </div>
          <p className="mt-1 text-xs leading-4 text-content-secondary">{issue.message}</p>
          <code className="mt-1 block truncate text-xs text-content-tertiary">{issue.json_pointer || '/'}</code>
        </button>
      ))}
    </div>
  );
}

interface DiagramSceneBoundaryState {
  key: string;
  scene: DiagramSceneV1 | null;
}

class DiagramSceneBoundary extends Component<
  Pick<PreviewRendererProps, 'descriptor'>,
  DiagramSceneBoundaryState
> {
  state: DiagramSceneBoundaryState = {
    key: storageKey(this.props.descriptor),
    scene: this.props.descriptor.diagram?.scene ?? null,
  };

  static getDerivedStateFromProps(
    props: Pick<PreviewRendererProps, 'descriptor'>,
    state: DiagramSceneBoundaryState,
  ): DiagramSceneBoundaryState | null {
    const key = storageKey(props.descriptor);
    const liveScene = props.descriptor.diagram?.scene ?? null;
    if (liveScene) {
      if (state.key !== key || state.scene !== liveScene) {
        return { key, scene: liveScene };
      }
      return null;
    }
    return state.key === key ? null : { key, scene: null };
  }

  render() {
    const { descriptor } = this.props;
    const liveScene = descriptor.diagram?.scene ?? null;
    const key = storageKey(descriptor);
    const scene = liveScene ?? (this.state.key === key ? this.state.scene : null);
    return (
      <DiagramCanvas
        descriptor={descriptor}
        scene={scene}
        showingPreviousRevision={liveScene === null && scene !== null}
      />
    );
  }
}

export function DiagramPreviewRenderer(props: PreviewRendererProps) {
  const { descriptor, onDirtyChange } = props;
  useEffect(() => onDirtyChange(false), [onDirtyChange]);
  return (
    <ReactFlowProvider>
      <DiagramSceneBoundary descriptor={descriptor} />
    </ReactFlowProvider>
  );
}
