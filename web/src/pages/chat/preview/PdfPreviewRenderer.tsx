import { useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Minus, Plus } from 'lucide-react';
import type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist';
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

import { Button } from '@/components/ui/button';
import type { PreviewErrorInfo } from '@/lib/preview/protocol';
import { PreviewErrorState } from './PreviewErrorState';
import type { PreviewRendererProps } from './renderer-types';

// The worker asset content hash predates the nginx `.mjs` MIME fix. Keep a
// URL-level revision so browsers that cached the old octet-stream response do
// not reuse its invalid Content-Type after an application upgrade.
const PDF_WORKER_CACHE_REVISION = 'module-mime-v1';

export function PdfPreviewRenderer({ descriptor }: PreviewRendererProps) {
  const url = descriptor.content?.url;
  if (!url) {
    return (
      <PreviewErrorState
        descriptor={descriptor}
        error={{ code: 'content_unavailable', params: {} }}
      />
    );
  }
  return (
    <PdfPreviewContent
      key={`${descriptor.revision}:${url}`}
      url={url}
      descriptor={descriptor}
    />
  );
}

function PdfPreviewContent({
  url,
  descriptor,
}: {
  url: string;
  descriptor: PreviewRendererProps['descriptor'];
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const documentRef = useRef<PDFDocumentProxy | null>(null);
  const pageRef = useRef<PDFPageProxy | null>(null);
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [zoom, setZoom] = useState(1.25);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<PreviewErrorInfo | null>(null);

  useEffect(() => {
    let disposed = false;
    let task: ReturnType<typeof import('pdfjs-dist')['getDocument']> | null = null;
    void import('pdfjs-dist').then(async (pdfjs) => {
      pdfjs.GlobalWorkerOptions.workerSrc = `${pdfWorkerUrl}?v=${PDF_WORKER_CACHE_REVISION}`;
      task = pdfjs.getDocument({
        url,
        rangeChunkSize: 64 * 1024,
        disableAutoFetch: true,
      });
      const document = await task.promise;
      if (disposed) return;
      documentRef.current = document;
      setPageCount(document.numPages);
    }).catch(() => {
      if (!disposed) setError({ code: 'render_failed', params: {} });
    }).finally(() => {
      if (!disposed) setLoading(false);
    });
    return () => {
      disposed = true;
      pageRef.current?.cleanup();
      pageRef.current = null;
      documentRef.current = null;
      if (task) void task.destroy();
    };
  }, [url]);

  useEffect(() => {
    const document = documentRef.current;
    const canvas = canvasRef.current;
    if (!document || !canvas || pageCount === 0) return;
    let disposed = false;
    let renderTask: { cancel: () => void; promise: Promise<unknown> } | null = null;
    void document.getPage(pageNumber).then((page) => {
      if (disposed) return;
      pageRef.current?.cleanup();
      pageRef.current = page;
      const viewport = page.getViewport({ scale: zoom * window.devicePixelRatio });
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Canvas is unavailable.');
      canvas.width = Math.ceil(viewport.width);
      canvas.height = Math.ceil(viewport.height);
      canvas.style.width = `${Math.ceil(viewport.width / window.devicePixelRatio)}px`;
      canvas.style.height = `${Math.ceil(viewport.height / window.devicePixelRatio)}px`;
      renderTask = page.render({ canvas, canvasContext: context, viewport });
      return renderTask.promise;
    }).catch((reason) => {
      if (!disposed && reason?.name !== 'RenderingCancelledException') {
        setError({ code: 'render_failed', params: {} });
      }
    });
    return () => {
      disposed = true;
      renderTask?.cancel();
    };
  }, [pageCount, pageNumber, zoom]);

  if (error) return <PreviewErrorState descriptor={descriptor} error={error} />;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-10 shrink-0 items-center justify-center gap-1 border-b border-edge-subtle">
        <Button
          variant="ghost"
          size="icon"
          disabled={pageNumber <= 1}
          aria-label="Previous page"
          onClick={() => setPageNumber((value) => Math.max(1, value - 1))}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <span className="min-w-24 text-center text-xs text-muted-foreground">
          {pageCount ? `${pageNumber} / ${pageCount}` : 'Loading…'}
        </span>
        <Button
          variant="ghost"
          size="icon"
          disabled={!pageCount || pageNumber >= pageCount}
          aria-label="Next page"
          onClick={() => setPageNumber((value) => Math.min(pageCount, value + 1))}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
        <span className="mx-1 h-4 w-px bg-edge-subtle" />
        <Button
          variant="ghost"
          size="icon"
          aria-label="Zoom out"
          onClick={() => setZoom((value) => Math.max(0.5, value - 0.25))}
        >
          <Minus className="h-4 w-4" />
        </Button>
        <span className="min-w-12 text-center text-xs">{Math.round(zoom * 100)}%</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="Zoom in"
          onClick={() => setZoom((value) => Math.min(3, value + 0.25))}
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-surface-sunken p-4 text-center">
        {loading ? <div className="p-4 text-sm text-muted-foreground">Loading PDF…</div> : null}
        <canvas ref={canvasRef} className="mx-auto bg-white shadow-sm" />
      </div>
    </div>
  );
}
