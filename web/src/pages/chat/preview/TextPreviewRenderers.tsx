import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Code2, Columns2, Eye, RotateCcw, Save } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { Button } from '@/components/ui/button';
import { ConfirmationDialog } from '@/components/ui/confirmation-dialog';
import { Textarea } from '@/components/ui/textarea';
import { PreviewApiError, createPreviewResourceSession } from '@/lib/api/previews';
import { useWritePreviewFile } from '@/lib/api/queries/previews';
import type { PreviewDescriptorV1, PreviewErrorInfo } from '@/lib/preview/protocol';
import { agentFilePathFromHref } from '@/lib/preview/protocol';
import {
  isPreviewSandboxLoaderMessage,
  loadPreviewSandboxDocument,
  PREVIEW_SANDBOX_LOADER_PATH,
} from '@/lib/preview/sandbox-loader';
import { cn } from '@/lib/utils';
import {
  buildFilePreviewHtmlDocument,
  FILE_PREVIEW_CHANNEL,
} from './html-preview-document';
import { PreviewErrorState } from './PreviewErrorState';
import { resolveCodePreviewLanguage } from './code-preview-language';
import type { PreviewRendererProps } from './renderer-types';

const CodePreviewEditor = lazy(() => import('./CodePreviewEditor').then(
  (module) => ({ default: module.CodePreviewEditor }),
));

type TextKind = 'text' | 'markdown' | 'html';
type DisplayMode = 'source' | 'preview' | 'split';

async function loadText(descriptor: PreviewDescriptorV1, signal?: AbortSignal): Promise<string> {
  if (typeof descriptor.content?.inlineText === 'string') {
    return descriptor.content.inlineText;
  }
  if (!descriptor.content?.url) return '';
  const sampleOnly = descriptor.content.truncated && descriptor.sizeBytes > 10 * 1024 * 1024;
  const response = await fetch(descriptor.content.url, {
    signal,
    headers: sampleOnly ? { Range: `bytes=0-${2 * 1024 * 1024 - 1}` } : undefined,
  });
  if (!response.ok && response.status !== 206) {
    throw new Error(`File content request failed: ${response.status}`);
  }
  return response.text();
}

function HtmlDocument({
  descriptor,
  html,
  onOpenFile,
}: {
  descriptor: PreviewDescriptorV1;
  html: string;
  onOpenFile?: (path: string) => void;
}) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [documentHtml, setDocumentHtml] = useState<string | null>(null);
  const [documentRevision, setDocumentRevision] = useState(0);
  const [error, setError] = useState<PreviewErrorInfo | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void createPreviewResourceSession(descriptor.fileRef, controller.signal).then(
      (session) => {
        setError(null);
        setDocumentHtml(buildFilePreviewHtmlDocument(html, session));
        setDocumentRevision((value) => value + 1);
      },
      () => {
        if (!controller.signal.aborted) {
          setError({ code: 'content_unavailable', params: {} });
        }
      },
    );
    return () => controller.abort();
  }, [descriptor.fileRef, descriptor.revision, html]);

  const loadSandboxDocument = useCallback(() => {
    loadPreviewSandboxDocument(iframeRef.current, documentHtml ?? '');
  }, [documentHtml]);

  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (event.source !== iframeRef.current?.contentWindow) return;
      if (event.origin !== 'null') return;
      if (isPreviewSandboxLoaderMessage(event.data)) {
        if (event.data.type === 'ready') loadSandboxDocument();
        else setError({ code: 'content_unavailable', params: {} });
        return;
      }
      const message = event.data as Record<string, unknown> | null;
      if (
        message?.channel === FILE_PREVIEW_CHANNEL
        && message.type === 'preview.open'
        && typeof message.path === 'string'
      ) {
        const path = agentFilePathFromHref(message.path);
        if (path) onOpenFile?.(path);
      }
    };
    window.addEventListener('message', listener);
    return () => window.removeEventListener('message', listener);
  }, [loadSandboxDocument, onOpenFile]);

  if (error) return <PreviewErrorState descriptor={descriptor} error={error} />;
  if (!documentHtml) return <div className="p-4 text-sm text-muted-foreground">Loading HTML preview…</div>;
  return (
    <iframe
      key={`${descriptor.revision}:${documentRevision}`}
      ref={iframeRef}
      title={descriptor.name}
      sandbox="allow-scripts"
      src={PREVIEW_SANDBOX_LOADER_PATH}
      onLoad={loadSandboxDocument}
      className="h-full min-h-[360px] w-full border-0 bg-white"
    />
  );
}

function RenderedText({
  kind,
  descriptor,
  value,
  onOpenFile,
}: {
  kind: TextKind;
  descriptor: PreviewDescriptorV1;
  value: string;
  onOpenFile?: (path: string) => void;
}) {
  if (kind === 'markdown') {
    return (
      <article className="prose prose-sm max-w-none p-5 dark:prose-invert">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
      </article>
    );
  }
  if (kind === 'html') {
    return <HtmlDocument descriptor={descriptor} html={value} onOpenFile={onOpenFile} />;
  }
  return <pre className="min-h-full whitespace-pre-wrap break-words p-4 font-mono text-xs">{value}</pre>;
}

function TextDocumentRenderer({
  descriptor,
  onDirtyChange,
  onOpenFile,
  kind,
}: PreviewRendererProps & { kind: TextKind }) {
  const write = useWritePreviewFile(descriptor.fileRef);
  const [source, setSource] = useState('');
  const [savedSource, setSavedSource] = useState('');
  const [mode, setMode] = useState<DisplayMode>(kind === 'text' ? 'source' : 'preview');
  const [editing, setEditing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<PreviewErrorInfo | null>(null);
  const [saveConflict, setSaveConflict] = useState(false);
  const [confirmOverwrite, setConfirmOverwrite] = useState(false);
  const editRevisionRef = useRef(descriptor.revision);
  const [editRevision, setEditRevision] = useState(descriptor.revision);
  const codeLanguage = kind === 'text' ? resolveCodePreviewLanguage(descriptor) : null;
  const dirty = editing && source !== savedSource;
  const conflict = saveConflict
    || (dirty && descriptor.revision !== editRevision);
  const dirtyRef = useRef(dirty);

  useEffect(() => {
    dirtyRef.current = dirty;
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);
  useEffect(() => () => onDirtyChange(false), [onDirtyChange]);

  useEffect(() => {
    if (dirtyRef.current && descriptor.revision !== editRevisionRef.current) {
      return;
    }
    const controller = new AbortController();
    void loadText(descriptor, controller.signal).then(
      (value) => {
        setLoadError(null);
        setSource(value);
        setSavedSource(value);
        editRevisionRef.current = descriptor.revision;
        setEditRevision(descriptor.revision);
        setSaveConflict(false);
      },
      () => {
        if (!controller.signal.aborted) {
          setLoadError({ code: 'content_unavailable', params: {} });
        }
      },
    ).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [descriptor]);

  const save = useCallback(async (overwrite = false) => {
    setSaveConflict(false);
    try {
      const result = await write.mutateAsync({
        expectedRevision: overwrite ? descriptor.revision : editRevisionRef.current,
        contentType: descriptor.contentType,
        content: source,
      });
      setSavedSource(source);
      editRevisionRef.current = result.revision;
      setEditRevision(result.revision);
      setEditing(false);
    } catch (reason) {
      if (reason instanceof PreviewApiError && reason.status === 409) {
        setSaveConflict(true);
      }
    }
  }, [descriptor.contentType, descriptor.revision, source, write]);

  const reload = useCallback(() => {
    setEditing(false);
    setSaveConflict(false);
    onDirtyChange(false);
    setSource(typeof descriptor.content?.inlineText === 'string' ? descriptor.content.inlineText : '');
    setSavedSource(typeof descriptor.content?.inlineText === 'string' ? descriptor.content.inlineText : '');
    editRevisionRef.current = descriptor.revision;
    setEditRevision(descriptor.revision);
  }, [descriptor, onDirtyChange]);

  const modes = useMemo(() => kind === 'text'
    ? [{ id: 'source' as const, label: 'Source', Icon: Code2 }]
    : [
        { id: 'source' as const, label: 'Source', Icon: Code2 },
        { id: 'preview' as const, label: 'Preview', Icon: Eye },
        { id: 'split' as const, label: 'Split', Icon: Columns2 },
      ], [kind]);

  if (loading) return <div className="p-4 text-sm text-muted-foreground">Loading text…</div>;
  if (loadError) return <PreviewErrorState descriptor={descriptor} error={loadError} />;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-10 shrink-0 items-center gap-1 border-b border-edge-subtle px-2">
        {modes.map(({ id, label, Icon }) => (
          <Button
            key={id}
            type="button"
            size="sm"
            variant={mode === id ? 'secondary' : 'ghost'}
            onClick={() => setMode(id)}
          >
            <Icon className="mr-1 h-3.5 w-3.5" />
            {label}
          </Button>
        ))}
        <div className="flex-1" />
        {descriptor.text?.mixedNewlines ? (
          <span className="text-xs text-amber-600">Mixed newlines will be normalized</span>
        ) : null}
        {descriptor.capabilities.edit && !editing ? (
          <Button size="sm" variant="outline" onClick={() => {
            editRevisionRef.current = descriptor.revision;
            setEditRevision(descriptor.revision);
            setEditing(true);
            setMode('source');
          }}>
            Edit
          </Button>
        ) : null}
        {editing ? (
          <Button size="sm" disabled={!dirty || write.isPending} onClick={() => void save()}>
            <Save className="mr-1 h-3.5 w-3.5" />
            Save
          </Button>
        ) : null}
      </div>
      {conflict ? (
        <div role="alert" className="flex items-center gap-2 border-b border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-950">
          <span className="min-w-0 flex-1">This file changed after editing began. Reload it or explicitly overwrite the latest revision.</span>
          <Button size="sm" variant="outline" onClick={reload}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" />
            Reload
          </Button>
          <Button
            size="sm"
            variant="destructive"
            onClick={() => setConfirmOverwrite(true)}
          >
            Overwrite
          </Button>
        </div>
      ) : null}
      {write.error && !conflict ? (
        <div role="alert" className="border-b border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          {write.error.message}
        </div>
      ) : null}
      <div className={cn(
        'grid min-h-0 flex-1 overflow-hidden',
        mode === 'split' ? 'grid-cols-2 divide-x divide-edge-subtle' : 'grid-cols-1',
      )}>
        {mode !== 'preview' ? (
          codeLanguage ? (
            <Suspense
              fallback={(
                <pre className="h-full overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs">
                  {source}
                </pre>
              )}
            >
              <CodePreviewEditor
                value={source}
                language={codeLanguage}
                readOnly={!editing}
                ariaLabel={`${descriptor.name} source`}
                onChange={setSource}
              />
            </Suspense>
          ) : editing ? (
            <Textarea
              aria-label={`${descriptor.name} source`}
              value={source}
              onChange={(event) => setSource(event.target.value)}
              className="h-full min-h-0 resize-none rounded-none border-0 font-mono text-xs focus-visible:ring-0"
            />
          ) : (
            <pre className="h-full overflow-auto whitespace-pre-wrap break-words p-4 font-mono text-xs">{source}</pre>
          )
        ) : null}
        {mode !== 'source' ? (
          <div className="min-h-0 overflow-auto">
            <RenderedText
              kind={kind}
              descriptor={descriptor}
              value={source}
              onOpenFile={onOpenFile}
            />
          </div>
        ) : null}
      </div>
      <ConfirmationDialog
        open={confirmOverwrite}
        onOpenChange={setConfirmOverwrite}
        title="Overwrite"
        description="Overwrite the latest file version with your current edits?"
        cancelLabel="Cancel"
        confirmLabel="Overwrite"
        pending={write.isPending}
        onConfirm={() => {
          setConfirmOverwrite(false);
          void save(true);
        }}
      />
    </div>
  );
}

export function TextPreviewRenderer(props: PreviewRendererProps) {
  return <TextDocumentRenderer {...props} kind="text" />;
}

export function MarkdownPreviewRenderer(props: PreviewRendererProps) {
  return <TextDocumentRenderer {...props} kind="markdown" />;
}

export function HtmlPreviewRenderer(props: PreviewRendererProps) {
  return <TextDocumentRenderer {...props} kind="html" />;
}
