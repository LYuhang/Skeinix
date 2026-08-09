import { useState } from 'react';
import { Pencil } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useWorkflow } from '@/lib/api/queries/workflow';
import { useUpdateWorkflowMeta } from '@/lib/api/mutations/workflows';

export function NameField({
  name,
  readOnly,
  onRename,
}: {
  name: string;
  readOnly: boolean;
  onRename: (next: string) => void;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);

  const startEdit = () => {
    if (readOnly) return;
    setDraft(name);
    setEditing(true);
  };

  if (editing && !readOnly) {
    const commit = () => {
      const trimmed = draft.trim();
      if (trimmed && trimmed !== name) onRename(trimmed);
      setEditing(false);
    };
    return (
      // Recessed input: inner shadow + muted fill so it reads as editable.
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit();
          if (e.key === 'Escape') {
            setDraft(name);
            setEditing(false);
          }
        }}
        className="w-56 rounded-md border border-border bg-muted px-2 py-1 text-sm shadow-inner focus:outline-none focus:ring-2 focus:ring-primary/40"
      />
    );
  }
  return (
    <div className="flex min-w-0 items-center gap-0.5">
      <button
        type="button"
        disabled={readOnly}
        onClick={startEdit}
        className="inline-flex min-h-8 max-w-[16rem] items-center truncate text-sm font-medium disabled:cursor-default"
        title={name}
      >
        {name || t('workflow.untitled', 'Untitled workflow')}
      </button>
      {!readOnly && (
        // Always-visible pencil so the name is discoverably editable.
        <button
          type="button"
          aria-label={t('workflow.rename', 'Rename workflow')}
          data-testid="rename-workflow"
          onClick={startEdit}
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

export function EditableWorkflowName({
  wfId,
  readOnly,
}: {
  wfId: string;
  readOnly: boolean;
}) {
  const { data } = useWorkflow(wfId);
  const rename = useUpdateWorkflowMeta();
  const name = data?.meta?.workflow_name ?? '';
  return (
    <NameField
      name={name}
      readOnly={readOnly}
      onRename={(next) => rename.mutate({ wfId, name: next })}
    />
  );
}
