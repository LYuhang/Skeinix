import { ExternalLink, FileText, Image as ImageIcon, Network } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { JsonTree } from './JsonTree';
import { CopyButton } from './CopyButton';
import {
  diagramPreviewPathFromStandardResult,
  type StandardContentBlock,
  type UniversalToolResultValue,
} from './parseStandardToolResult';
const MAX_INLINE_TEXT_CHARS = 120_000;
const MAX_INLINE_IMAGE_BASE64_CHARS = 5_600_000;

function boundedText(value: string): string {
  if (value.length <= MAX_INLINE_TEXT_CHARS) return value;
  return `${value.slice(0, MAX_INLINE_TEXT_CHARS)}\n\n… output truncated (${value.length.toLocaleString()} characters total)`;
}

function safeExternalUri(value: string): string | null {
  try {
    const url = new URL(value, window.location.origin);
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
  } catch {
    return null;
  }
}

function ContentBlock({ block }: { block: StandardContentBlock }) {
  if (block.type === 'text' && typeof block.text === 'string') {
    return <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-surface-sunken p-2.5 font-sans text-xs leading-5">{boundedText(block.text)}</pre>;
  }
  if (block.type === 'image' && block.data && block.mimeType?.startsWith('image/')) {
    if (block.data.length > MAX_INLINE_IMAGE_BASE64_CHARS) {
      return <div className="rounded-md border border-edge-subtle bg-surface-sunken p-3 text-xs text-content-tertiary">Image output is too large to render inline.</div>;
    }
    return <div className="rounded-md border border-edge-subtle bg-surface-sunken p-2"><div className="mb-2 flex items-center gap-1.5 text-xs text-content-tertiary"><ImageIcon className="h-3.5 w-3.5" />{block.mimeType}</div><img className="max-h-80 max-w-full rounded object-contain" src={`data:${block.mimeType};base64,${block.data}`} alt={block.name || 'Tool output'} /></div>;
  }
  if (block.type === 'resource_link' && block.uri) {
    const href = safeExternalUri(block.uri);
    if (!href) return <div className="rounded-md border border-edge-subtle px-3 py-2 text-xs text-content-tertiary">Blocked unsafe resource link</div>;
    return <a href={href} target="_blank" rel="noreferrer" className="flex min-h-10 items-center gap-2 rounded-md border border-edge-subtle px-3 text-xs text-state-info hover:bg-surface-hover"><ExternalLink className="h-3.5 w-3.5" /><span className="min-w-0 flex-1 truncate">{block.name || block.uri}</span></a>;
  }
  if (block.type === 'resource' && block.resource) {
    return <div className="rounded-md border border-edge-subtle"><div className="flex items-center gap-2 border-b border-edge-subtle px-3 py-2 text-xs text-content-tertiary"><FileText className="h-3.5 w-3.5" /><span className="truncate">{block.resource.uri || block.resource.mimeType || 'Resource'}</span></div><pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words p-3 text-xs leading-5">{block.resource.text ? boundedText(block.resource.text) : '(binary resource)'}</pre></div>;
  }
  const raw = JSON.stringify(block, null, 2);
  return <div className="relative"><pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-surface-sunken p-2.5 font-mono text-xs">{raw}</pre><div className="absolute right-1 top-1"><CopyButton value={raw} label="Copy raw JSON" /></div></div>;
}

export function UniversalToolResult({
  value,
  wfId,
  onOpenFile,
}: {
  value: UniversalToolResultValue;
  wfId?: string;
  onOpenFile?: (path: string) => void;
}) {
  const previewPath = diagramPreviewPathFromStandardResult(value);
  return (
    <div className="space-y-2" data-role="universal-tool-result">
      {previewPath && onOpenFile ? (
        <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => onOpenFile(previewPath)}>
          <Network className="mr-2 h-3.5 w-3.5" />
          Open diagram
        </Button>
      ) : null}
      {value.structuredContent !== undefined ? (
        <JsonTree output={{ content_type: 'application/json', data: JSON.stringify(value.structuredContent) }} abstract="Structured result" status={value.isError ? 'error' : 'success'} wfId={wfId} />
      ) : null}
      {value.content.map((block, index) => <ContentBlock key={`${block.type}:${index}`} block={block} />)}
    </div>
  );
}
