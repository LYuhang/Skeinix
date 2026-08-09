import type { PreviewRendererProps } from './renderer-types';
import { PreviewErrorState } from './PreviewErrorState';

export function UnsupportedPreviewRenderer({ descriptor }: PreviewRendererProps) {
  return <PreviewErrorState descriptor={descriptor} />;
}
