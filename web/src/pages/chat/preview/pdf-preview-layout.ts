const PREVIEW_HORIZONTAL_PADDING = 32;
export const MIN_PDF_ZOOM = 0.2;
const MAX_INITIAL_PDF_ZOOM = 1.25;

export function initialPdfPreviewZoom(
  naturalWidth: number,
  containerWidth: number,
): number {
  const availableWidth = containerWidth - PREVIEW_HORIZONTAL_PADDING;
  if (naturalWidth <= 0 || availableWidth <= 0) {
    return MAX_INITIAL_PDF_ZOOM;
  }
  return Math.max(
    MIN_PDF_ZOOM,
    Math.min(
      MAX_INITIAL_PDF_ZOOM,
      Math.floor((availableWidth / naturalWidth) * 20) / 20,
    ),
  );
}
