export interface StandardContentBlock {
  type: string;
  text?: string;
  data?: string;
  mimeType?: string;
  uri?: string;
  name?: string;
  resource?: { text?: string; uri?: string; mimeType?: string };
}

export interface UniversalToolResultValue {
  structuredContent?: unknown;
  content: StandardContentBlock[];
  isError?: boolean;
  raw: unknown;
}

export const MAX_STANDARD_BLOCKS = 100;
const MAX_NESTED_JSON_TEXT_CHARS = 1_000_000;

function object(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function diagramPreviewPath(value: unknown): string | null {
  if (!object(value)) return null;
  const preview = value.preview_ref;
  if (!object(preview)) return null;
  const fileRef = preview.fileRef;
  if (!object(fileRef)) return null;
  const path = fileRef.path;
  return typeof path === 'string' && path.startsWith('/data/diagrams/') ? path : null;
}

export interface DiagramDraftPreviewTarget {
  draftId: string;
  chatId: string;
  targetPath: `/data/${string}`;
  title: string;
}

function diagramDraftPreviewTarget(value: unknown): DiagramDraftPreviewTarget | null {
  if (!object(value)) return null;
  const preview = value.draft_preview_ref;
  if (!object(preview) || preview.kind !== 'diagram_draft') return null;
  if (
    typeof preview.draftId !== 'string'
    || typeof preview.chatId !== 'string'
    || typeof preview.targetPath !== 'string'
    || !preview.targetPath.startsWith('/data/')
  ) return null;
  return {
    draftId: preview.draftId,
    chatId: preview.chatId,
    targetPath: preview.targetPath as `/data/${string}`,
    title: typeof preview.title === 'string' && preview.title ? preview.title : 'Diagram draft',
  };
}

export function diagramDraftPreviewFromStandardResult(
  value: UniversalToolResultValue | null,
): DiagramDraftPreviewTarget | null {
  if (!value) return null;
  const direct = diagramDraftPreviewTarget(value.structuredContent);
  if (direct) return direct;
  for (const block of value.content) {
    if (
      block.type !== 'text'
      || typeof block.text !== 'string'
      || block.text.length > MAX_NESTED_JSON_TEXT_CHARS
      || !block.text.trimStart().startsWith('{')
    ) continue;
    try {
      const target = diagramDraftPreviewTarget(JSON.parse(block.text) as unknown);
      if (target) return target;
    } catch {
      // Non-JSON text remains ordinary tool output.
    }
  }
  return null;
}

/**
 * MCP clients expose structured tool output in either `structuredContent` or
 * as a JSON object serialized into a text content block. The latter is the
 * shape currently emitted by the LangChain adapter. Keep this bounded and
 * narrowly scoped to the signed Diagram Preview reference; arbitrary nested
 * JSON is never rendered as trusted markup.
 */
export function diagramPreviewPathFromStandardResult(
  value: UniversalToolResultValue | null,
): string | null {
  if (!value) return null;
  const direct = diagramPreviewPath(value.structuredContent);
  if (direct) return direct;
  for (const block of value.content) {
    if (
      block.type !== 'text'
      || typeof block.text !== 'string'
      || block.text.length > MAX_NESTED_JSON_TEXT_CHARS
      || !block.text.trimStart().startsWith('{')
    ) continue;
    try {
      const nested = JSON.parse(block.text) as unknown;
      const path = diagramPreviewPath(nested);
      if (path) return path;
    } catch {
      // Ordinary text remains ordinary text; a parse miss is not an error.
    }
  }
  return null;
}

export function parseStandardToolResult(result: string | undefined): UniversalToolResultValue | null {
  if (!result) return null;
  let raw: unknown;
  try { raw = JSON.parse(result); } catch { return null; }
  // LangChain's MCP adapter serializes the protocol `content` list directly,
  // while other clients retain the enclosing `{ content, structuredContent }`
  // result object. Normalize both protocol-preserving shapes.
  if (Array.isArray(raw)) {
    return {
      content: raw.filter(object).slice(0, MAX_STANDARD_BLOCKS)
        .map((item) => item as unknown as StandardContentBlock),
      raw,
    };
  }
  if (!object(raw)) return { content: [], structuredContent: raw, raw };
  const structuredContent = raw.structuredContent ?? raw.structured_content;
  const content = Array.isArray(raw.content)
    ? raw.content.filter(object).slice(0, MAX_STANDARD_BLOCKS).map((item) => item as unknown as StandardContentBlock)
    : [];
  if (structuredContent === undefined && !content.length) {
    return { content: [], structuredContent: raw, raw };
  }
  return { structuredContent, content, isError: raw.isError === true, raw };
}
