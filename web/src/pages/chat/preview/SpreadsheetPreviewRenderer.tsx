import { lazy, Suspense } from 'react';
import { useTranslation } from 'react-i18next';

import type { PreviewRendererProps } from './renderer-types';

const NativeWorkbookPreview = lazy(() => import('./NativeWorkbookPreview').then(
  (module) => ({ default: module.NativeWorkbookPreview }),
));

const StructuredTablePreview = lazy(() => import('./StructuredTablePreview').then(
  (module) => ({ default: module.StructuredTablePreview }),
));

function SpreadsheetLoadingState() {
  const { t } = useTranslation();
  return (
    <div className="p-4 text-sm text-muted-foreground">
      {t('preview.table.loading', 'Loading table…')}
    </div>
  );
}

export function SpreadsheetPreviewRenderer(props: PreviewRendererProps) {
  const Renderer = props.descriptor.detectedType === 'spreadsheet'
    ? NativeWorkbookPreview
    : StructuredTablePreview;

  return (
    <Suspense fallback={<SpreadsheetLoadingState />}>
      <Renderer {...props} />
    </Suspense>
  );
}
