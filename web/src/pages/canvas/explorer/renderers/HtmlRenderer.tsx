import type { VfsReadOut } from '@/lib/api/vfs';

export function HtmlRenderer({ entry }: { entry: VfsReadOut }) {
  // Agent-produced HTML is untrusted — show the SOURCE, escaped. Putting the
  // string in a text node makes React escape it; we never inject it as DOM.
  return <pre className="overflow-auto whitespace-pre-wrap break-words text-xs">{entry.content}</pre>;
}
