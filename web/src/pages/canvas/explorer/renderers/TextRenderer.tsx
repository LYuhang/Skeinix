import type { VfsReadOut } from '@/lib/api/vfs';

export function TextRenderer({ entry }: { entry: VfsReadOut }) {
  return <pre className="overflow-auto whitespace-pre-wrap break-words text-xs">{entry.content}</pre>;
}
