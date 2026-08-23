import { describe, expect, it } from 'vitest';

import { initialPdfPreviewZoom } from '../pdf-preview-layout';

describe('initial PDF Preview zoom', () => {
  it('keeps readable portrait pages at the preferred 125 percent', () => {
    expect(initialPdfPreviewZoom(612, 825)).toBe(1.25);
  });

  it('fits a wide presentation page into a narrow Preview pane', () => {
    expect(initialPdfPreviewZoom(768, 825)).toBe(1);
  });

  it('fits a presentation page into the actual narrow Preview pane', () => {
    expect(initialPdfPreviewZoom(960, 400)).toBe(0.35);
  });

  it('never shrinks an exceptionally wide page below 20 percent', () => {
    expect(initialPdfPreviewZoom(10_000, 825)).toBe(0.2);
  });
});
