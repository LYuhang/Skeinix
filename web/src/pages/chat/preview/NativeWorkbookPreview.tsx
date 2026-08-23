import { useCallback, useMemo, useState } from 'react';
import FileViewer, { type FileViewerProps, type ViewerState } from '@file-viewer/react';
import { spreadsheetRenderer } from '@file-viewer/renderer-spreadsheet';
import { useTranslation } from 'react-i18next';
import { useTheme } from 'next-themes';

import { fileRefKey, type PreviewErrorInfo } from '@/lib/preview/protocol';
import { PreviewErrorState } from './PreviewErrorState';
import type { PreviewRendererProps } from './renderer-types';

export function NativeWorkbookPreview({
  descriptor,
  loadAllowed,
}: PreviewRendererProps) {
  const { i18n } = useTranslation();
  const { resolvedTheme } = useTheme();
  const [renderError, setRenderError] = useState<PreviewErrorInfo | null>(null);
  const url = descriptor.content?.url;
  const viewerKey = `${fileRefKey(descriptor.fileRef)}:${descriptor.revision}`;
  const options = useMemo<NonNullable<FileViewerProps['options']>>(() => ({
    rendererMode: 'replace' as const,
    // The renderer package narrows its mount element to HTMLDivElement while
    // core's public registry accepts HTMLElement. Runtime contracts are the
    // same; isolate the upstream generic-variance mismatch at this boundary.
    renderers: [spreadsheetRenderer] as unknown as NonNullable<FileViewerProps['options']>['renderers'],
    styleIsolation: 'shadow' as const,
    theme: resolvedTheme === 'dark' ? 'dark' as const : 'light' as const,
    i18n: {
      locale: i18n.resolvedLanguage?.toLowerCase().startsWith('zh')
        ? 'zh-CN' as const
        : 'en-US' as const,
    },
    toolbar: {
      download: false,
      print: false,
      exportHtml: false,
      theme: false,
      search: true,
      zoom: true,
    },
    spreadsheet: {
      worker: false,
      resizableColumns: true,
      resizableRows: true,
    },
  }), [i18n.resolvedLanguage, resolvedTheme]);
  const handleStateChange = useCallback((state: ViewerState) => {
    if (state.error) {
      setRenderError({ code: 'render_failed', params: {} });
    }
  }, []);

  if (!loadAllowed) return null;
  if (!url) {
    return (
      <PreviewErrorState
        descriptor={descriptor}
        error={{ code: 'content_unavailable', params: {} }}
      />
    );
  }
  if (renderError) {
    return <PreviewErrorState descriptor={descriptor} error={renderError} />;
  }

  return (
    <FileViewer
      key={viewerKey}
      className="h-full min-h-0 w-full"
      url={url}
      name={descriptor.name}
      size={descriptor.sizeBytes}
      type={descriptor.name.split('.').pop() ?? 'xlsx'}
      options={options}
      onStateChange={handleStateChange}
      data-role="native-workbook-preview"
    />
  );
}
