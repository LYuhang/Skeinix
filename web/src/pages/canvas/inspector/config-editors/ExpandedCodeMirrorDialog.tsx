import type { Extension } from '@codemirror/state';
import { useTranslation } from 'react-i18next';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { CodeMirrorField } from './CodeMirrorField';

interface ExpandedCodeMirrorDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  meta?: string;
  value: string;
  onCommit: (next: string) => void;
  readOnly?: boolean;
  extensions?: Extension[];
  placeholder?: string;
  testId?: string;
}

export function ExpandedCodeMirrorDialog({
  open,
  onOpenChange,
  title,
  meta,
  value,
  onCommit,
  readOnly,
  extensions,
  placeholder,
  testId,
}: ExpandedCodeMirrorDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="grid max-h-[86vh] w-[min(1120px,calc(100vw-48px))] max-w-none grid-rows-[auto_minmax(0,1fr)_auto] gap-4 overflow-hidden p-0"
        data-testid={testId}
      >
        <DialogHeader className="surface-topbar border-b px-5 py-4 pr-12">
          <div className="flex min-w-0 items-center gap-2">
            <DialogTitle className="truncate text-section tracking-normal">
              {title}
            </DialogTitle>
            {meta && (
              <span className="shrink-0 rounded border border-edge-subtle bg-surface-sunken px-1.5 py-0.5 text-xs font-medium text-content-secondary">
                {meta}
              </span>
            )}
          </div>
        </DialogHeader>

        <div className="min-h-0 px-5">
          <CodeMirrorField
            value={value}
            onCommit={onCommit}
            commitOnChange
            readOnly={readOnly}
            extensions={extensions}
            placeholder={placeholder}
            minHeight="min(66vh, 620px)"
            maxHeight="min(66vh, 620px)"
            className="h-full rounded-lg text-[13px]"
            data-testid={testId ? `${testId}-editor` : undefined}
          />
        </div>

        <DialogFooter className="border-t bg-muted/20 px-5 py-3">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={() => onOpenChange(false)}
          >
            {t('done', 'Done')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
