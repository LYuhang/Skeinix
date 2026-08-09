/**
 * Stream 8 (M5) — the themed confirm shown when a navigation is blocked by
 * `useUnsavedGuard` (the in-app route guard). Three exits:
 *
 *   - Save     → commit the draft, then proceed once it succeeds.
 *   - Discard  → proceed immediately, dropping the unsaved draft.
 *   - Cancel   → stay on the page.
 *
 * Headless mechanics (blocker.proceed/reset, the commit mutation) live in the
 * page; this component is presentation + the three callbacks. The browser
 * close/refresh path uses the native `beforeunload` prompt instead — the
 * browser owns that dialog and won't render this one.
 */
import { useTranslation } from 'react-i18next';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

export interface UnsavedChangesDialogProps {
  open: boolean;
  /** Save is in flight — disable the buttons to prevent a double-commit. */
  saving: boolean;
  onSave: () => void;
  onDiscard: () => void;
  onCancel: () => void;
}

export function UnsavedChangesDialog({
  open,
  saving,
  onSave,
  onDiscard,
  onCancel,
}: UnsavedChangesDialogProps) {
  const { t } = useTranslation();
  return (
    <Dialog
      open={open}
      // Closing via overlay / Esc == Cancel (stay on the page).
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <DialogContent data-role="unsaved-changes-dialog">
        <DialogHeader>
          <DialogTitle>{t('unsaved_title', 'Unsaved changes')}</DialogTitle>
          <DialogDescription>
            {t(
              'unsaved_body',
              'You have unsaved edits to this workflow. Save them before leaving, or discard them?',
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={saving}
            onClick={onCancel}
            data-role="unsaved-cancel"
          >
            {t('unsaved_cancel', 'Cancel')}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={saving}
            onClick={onDiscard}
            data-role="unsaved-discard"
          >
            {t('unsaved_discard', 'Discard')}
          </Button>
          <Button
            type="button"
            disabled={saving}
            onClick={onSave}
            data-role="unsaved-save"
          >
            {saving ? t('unsaved_saving', 'Saving…') : t('unsaved_save', 'Save')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
