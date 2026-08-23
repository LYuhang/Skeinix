import { resolvePreviewResourceUrl } from '@/lib/api/previews';
import type { PreviewResourceSessionV1 } from '@/lib/preview/protocol';

/** Resolve a Markdown image without exposing workspace identity to the page. */
export function resolveMarkdownImageUrl(
  value: string,
  session: PreviewResourceSessionV1,
): string | null {
  const source = value.trim();
  if (!source || source.includes('\\')) return null;
  if (/^https?:\/\//i.test(source) || /^data:image\//i.test(source)) return source;
  if (source.startsWith('//') || /^[a-z][a-z\d+.-]*:/i.test(source)) return null;
  if (source.startsWith('/')) return resolvePreviewResourceUrl(source, session);

  try {
    const base = new URL(session.baseUrl);
    const resolved = new URL(source, base);
    return resolved.origin === base.origin ? resolved.href : null;
  } catch {
    return null;
  }
}
