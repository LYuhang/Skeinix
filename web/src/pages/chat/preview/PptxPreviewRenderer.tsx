import { useEffect, useRef, useState } from 'react';
import type { PptxViewer } from '@aiden0z/pptx-renderer';

import type { PreviewErrorInfo } from '@/lib/preview/protocol';
import { PreviewErrorState } from './PreviewErrorState';
import type { PreviewRendererProps } from './renderer-types';

export function PptxPreviewRenderer({ descriptor, loadAllowed }: PreviewRendererProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const slidesRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<PptxViewer | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<PreviewErrorInfo | null>(null);

  useEffect(() => {
    const container = slidesRef.current;
    const scrollContainer = scrollRef.current;
    const url = descriptor.content?.url;
    if (!container || !scrollContainer || !url || !loadAllowed) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    container.replaceChildren();
    void fetch(url, { signal: controller.signal }).then((response) => {
      if (!response.ok) throw new Error(`PPTX request failed: ${response.status}`);
      return response.arrayBuffer();
    }).then(async (buffer) => {
      const { PptxViewer: Viewer } = await import('@aiden0z/pptx-renderer/browser');
      if (controller.signal.aborted) return;
      const viewer = new Viewer(container, {
        fitMode: 'contain',
        scrollContainer,
        lazyMedia: true,
        lazySlides: true,
        pdfjs: false,
        zipLimits: {
          maxEntries: 10_000,
          maxEntryUncompressedBytes: 100 * 1024 * 1024,
          maxTotalUncompressedBytes: 500 * 1024 * 1024,
          maxMediaBytes: 300 * 1024 * 1024,
          maxConcurrency: 4,
        },
      });
      viewerRef.current = viewer;
      await viewer.open(buffer, {
        renderMode: 'list',
        signal: controller.signal,
        lazyMedia: true,
        lazySlides: true,
        listOptions: {
          windowed: true,
          initialSlides: 4,
          batchSize: 2,
          overscanViewport: 1.5,
          showSlideLabels: true,
        },
      });
    }).catch(() => {
      if (!controller.signal.aborted) {
        setError({ code: 'render_failed', params: {} });
      }
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => {
      controller.abort();
      viewerRef.current?.destroy();
      viewerRef.current = null;
      container.replaceChildren();
    };
  }, [descriptor.content?.url, descriptor.revision, loadAllowed]);

  return (
    <div ref={scrollRef} className="h-full overflow-auto bg-surface-sunken p-4">
      <div className="mb-3 text-center text-xs text-muted-foreground">
        Quick preview may differ from Microsoft PowerPoint.
      </div>
      {loading ? <div className="p-4 text-center text-sm text-muted-foreground">Loading PPTX…</div> : null}
      {error ? <PreviewErrorState descriptor={descriptor} error={error} /> : null}
      <div ref={slidesRef} className="mx-auto max-w-6xl" />
    </div>
  );
}
