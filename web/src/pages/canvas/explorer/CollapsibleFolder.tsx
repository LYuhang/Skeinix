import type { ReactNode } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { FileTypeIcon } from '@/components/presentation/FileTypeIcon';

export interface CollapsibleFolderProps {
  label: string;
  path?: string;
  depth: number;
  open: boolean;
  onToggle: () => void;
  treeItem?: boolean;
  selected?: boolean;
  onSelect?: () => void;
  trailing?: ReactNode;
  /** Optional hover tooltip — used to surface the taxonomy semantics of the
   *  canonical top folders (mount/data/memory/logs/skills). */
  title?: string;
  children?: ReactNode;
}

export function CollapsibleFolder({ label, path = label, depth, open, onToggle, treeItem = false, selected = false, onSelect, trailing, title, children }: CollapsibleFolderProps) {
  return (
    <div>
      <button
        type="button"
        role={treeItem ? 'treeitem' : undefined}
        aria-level={treeItem ? depth + 1 : undefined}
        aria-selected={treeItem ? selected : undefined}
        data-tree-full-path={treeItem ? path : undefined}
        data-tree-name={treeItem ? label.toLocaleLowerCase() : undefined}
        data-tree-kind={treeItem ? 'folder' : undefined}
        tabIndex={treeItem ? (selected ? 0 : -1) : 0}
        className="interactive-row flex w-full items-center gap-1.5 rounded py-1 text-left text-ui aria-selected:bg-surface-hover aria-selected:text-content-primary"
        style={{ paddingLeft: depth * 12 + 8 }}
        onClick={() => {
          onSelect?.();
          onToggle();
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            onToggle();
          } else if (treeItem && event.key === 'ArrowRight' && !open) {
            event.preventDefault();
            onToggle();
          } else if (treeItem && event.key === 'ArrowLeft') {
            event.preventDefault();
            if (open) onToggle();
            else {
              const ownPath = event.currentTarget.dataset.treeFullPath ?? '';
              const parentPath = ownPath.slice(0, ownPath.lastIndexOf('/'));
              const tree = event.currentTarget.closest('[role="tree"]');
              const parent = Array.from(tree?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? [])
                .find((item) => item.dataset.treeFullPath === parentPath);
              parent?.focus();
            }
          }
        }}
        aria-expanded={open}
        title={title}
      >
        <span
          aria-hidden="true"
          className="grid h-5 w-5 shrink-0 place-items-center rounded"
        >
          {open ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
        </span>
        <FileTypeIcon
          fileName={label}
          directory
          open={open}
          className="size-5 rounded"
        />
        <span className="truncate font-medium">{label}</span>
        {trailing != null && <span className="ml-auto pr-2">{trailing}</span>}
      </button>
      {open && children}
    </div>
  );
}
