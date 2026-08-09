import { useEffect, useRef, useState } from 'react';
import { FileText } from 'lucide-react';

import type { PreviewErrorInfo } from '@/lib/preview/protocol';
import { prepareDocxPages } from './docx-page-layout';
import { PreviewErrorState } from './PreviewErrorState';
import type { PreviewRendererProps } from './renderer-types';

export function DocxPreviewRenderer({ descriptor, loadAllowed }: PreviewRendererProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const styleRef = useRef<HTMLDivElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<PreviewErrorInfo | null>(null);
  const [pageCount, setPageCount] = useState(0);

  useEffect(() => {
    const container = containerRef.current;
    const styleContainer = styleRef.current;
    const viewport = viewportRef.current;
    const url = descriptor.content?.url;
    if (!container || !styleContainer || !viewport || !url || !loadAllowed) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setPageCount(0);
    container.replaceChildren();
    styleContainer.replaceChildren();
    let resizeObserver: ResizeObserver | undefined;
    void Promise.all([
      fetch(url, { signal: controller.signal }).then((response) => {
        if (!response.ok) throw new Error(`DOCX request failed: ${response.status}`);
        return response.blob();
      }),
      import('docx-preview'),
    ]).then(async ([blob, docx]) => {
      await docx.renderAsync(blob, container, styleContainer, {
        renderAltChunks: false,
        useBase64URL: true,
        renderChanges: false,
        // Word and LibreOffice emit these hints. Respecting them provides
        // genuine multi-page scrolling without expensive browser reflow-based
        // pagination.
        ignoreLastRenderedPageBreak: false,
        experimental: true,
        debug: false,
      });
      if (controller.signal.aborted) return;
      const layout = () => {
        const result = prepareDocxPages(container, viewport.clientWidth);
        setPageCount(result.pageCount);
      };
      layout();
      if (typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(layout);
        resizeObserver.observe(viewport);
      }
    }).catch(() => {
      if (!controller.signal.aborted) {
        setError({ code: 'render_failed', params: {} });
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => {
      controller.abort();
      resizeObserver?.disconnect();
      container.replaceChildren();
      styleContainer.replaceChildren();
    };
  }, [descriptor.content?.url, descriptor.revision, loadAllowed]);

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#e5e7eb] dark:bg-[#17191d]">
      <div className="flex min-h-9 shrink-0 items-center gap-2 border-b border-black/10 bg-white/90 px-3 text-xs text-slate-600 backdrop-blur dark:border-white/10 dark:bg-[#24262b]/95 dark:text-slate-300">
        <FileText className="h-3.5 w-3.5" aria-hidden="true" />
        <span className="font-medium">Document preview</span>
        {pageCount > 0 ? (
          <span className="text-slate-400 dark:text-slate-500">
            {pageCount} {pageCount === 1 ? 'page' : 'pages'}
          </span>
        ) : null}
        <span className="ml-auto text-slate-400 dark:text-slate-500">Read only</span>
      </div>
      <div ref={viewportRef} className="relative min-h-0 flex-1 overflow-auto">
        {loading ? (
          <div className="absolute inset-x-0 top-6 z-10 mx-auto w-fit rounded-full bg-white/90 px-3 py-1.5 text-xs text-slate-500 shadow-sm dark:bg-[#24262b] dark:text-slate-300">
            Loading document…
          </div>
        ) : null}
        {error ? <PreviewErrorState descriptor={descriptor} error={error} /> : null}
        <div ref={styleRef} className="hidden" aria-hidden="true" />
        <div
          ref={containerRef}
          className="mx-auto min-h-40 min-w-0 overflow-visible pb-6"
          data-testid="docx-document-canvas"
        />
      </div>
    </div>
  );
}
