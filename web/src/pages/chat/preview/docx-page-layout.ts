const FALLBACK_PAGE_WIDTH_PX = 816;
const FALLBACK_PAGE_HEIGHT_PX = 1056;
const PAGE_GUTTER_PX = 32;

/**
 * docx-preview preserves page geometry when the OOXML contains section
 * properties. Agent-generated and older documents sometimes omit those
 * properties entirely; without a fallback the library collapses the page to
 * the width and height of its text, which no longer reads like a document.
 */
export function prepareDocxPages(
  body: HTMLElement,
  viewportWidth: number,
): { pageCount: number; scale: number } {
  const wrapper = body.querySelector<HTMLElement>('.docx-wrapper');
  const pages = Array.from(body.querySelectorAll<HTMLElement>('section.docx'));
  if (!wrapper || pages.length === 0) return { pageCount: 0, scale: 1 };

  for (const page of pages) {
    page.dataset.previewPage = 'true';
    if (!page.style.width) page.style.width = `${FALLBACK_PAGE_WIDTH_PX}px`;
    if (!page.style.height && !page.style.minHeight) {
      page.style.minHeight = `${FALLBACK_PAGE_HEIGHT_PX}px`;
    }
    if (!page.style.padding) page.style.padding = '88px 72px 96px';
    page.style.border = '1px solid rgba(15, 23, 42, 0.10)';
    page.style.borderRadius = '2px';
    page.style.boxShadow = '0 2px 8px rgba(15, 23, 42, 0.12), 0 18px 48px rgba(15, 23, 42, 0.08)';
  }

  // Measure without the previous zoom so resizing the Preview pane cannot
  // compound the scale. CSS zoom is intentional here: unlike transform it
  // contributes the scaled height to layout, preserving continuous scrolling.
  wrapper.style.zoom = '1';
  wrapper.style.width = 'max-content';
  wrapper.style.maxWidth = 'none';
  wrapper.style.margin = '0 auto';
  wrapper.style.padding = '20px 0 1px';
  wrapper.style.background = 'transparent';
  wrapper.style.gap = '24px';

  const naturalWidth = Math.max(
    ...pages.map((page) => page.getBoundingClientRect().width || FALLBACK_PAGE_WIDTH_PX),
  );
  const availableWidth = Math.max(0, viewportWidth - PAGE_GUTTER_PX);
  const scale = availableWidth > 0 ? Math.min(1, availableWidth / naturalWidth) : 1;
  wrapper.style.zoom = String(scale);
  return { pageCount: pages.length, scale };
}
