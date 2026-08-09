import { CopyButton } from './CopyButton';
import { Button } from '@/components/ui/button';
import { ExternalLink } from 'lucide-react';

interface DiffBlockProps {
  diff: string;
  path?: string;
  onOpenFile?: (path: string) => void;
}

function lineTone(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) {
    return 'bg-surface-raised text-muted-foreground';
  }
  if (line.startsWith('@@')) return 'bg-state-info/10 text-state-info';
  if (line.startsWith('+')) return 'bg-state-success/10 text-state-success';
  if (line.startsWith('-')) return 'bg-state-danger/10 text-state-danger';
  return 'text-foreground/80';
}

export function DiffBlock({ diff, path, onOpenFile }: DiffBlockProps) {
  const lines = diff.split('\n');
  return (
    <section
      className="overflow-hidden rounded-md border border-edge-subtle bg-surface-sunken"
      aria-label={path ? `Changes to ${path}` : 'File changes'}
      data-role="diff-block"
    >
      <header className="flex min-h-9 items-center gap-2 border-b border-edge-subtle px-3">
        <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
          {path || 'Unified diff'}
        </span>
        {path && onOpenFile && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1 px-2 text-xs"
            onClick={() => onOpenFile(path)}
          >
            <ExternalLink className="h-3.5 w-3.5" />
            Open file
          </Button>
        )}
        <CopyButton value={diff} label="Copy diff" />
      </header>
      <div className="max-h-96 overflow-auto py-1 font-mono text-xs leading-5">
        {lines.map((line, index) => (
          <div
            // Diff line order is stable and duplicate lines are valid.
            key={`${index}:${line}`}
            className={`grid grid-cols-[3rem_minmax(0,1fr)] px-2 ${lineTone(line)}`}
            aria-label={line.startsWith('+') && !line.startsWith('+++')
              ? `Added line ${index + 1}: ${line.slice(1)}`
              : line.startsWith('-') && !line.startsWith('---')
                ? `Deleted line ${index + 1}: ${line.slice(1)}`
                : `Context line ${index + 1}: ${line}`}
          >
            <span className="select-none pr-3 text-right tabular-nums text-muted-foreground/55">
              {index + 1}
            </span>
            <span className="whitespace-pre-wrap break-words">{line || ' '}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
