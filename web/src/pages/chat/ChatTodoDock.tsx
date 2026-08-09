import { ChevronDown, ChevronUp, ListTodo } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { StatusDot, type SemanticStatus } from '@/components/ui/status';
import type { TodoItem } from '@/stores/chat-stream';
import { cn } from '@/lib/utils';

export interface ChatTodoDockProps {
  items: TodoItem[];
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}

export function ChatTodoDock({ items, collapsed, onCollapsedChange }: ChatTodoDockProps) {
  const { t } = useTranslation();
  const hasOpenTasks = items.some((item) => item.status !== 'done');
  if (!hasOpenTasks) return null;

  const total = items.length;
  const done = items.filter((item) => item.status === 'done').length;
  const active = items.filter((item) => item.status === 'in_progress').length;
  const left = total - done;
  const statusLabel = (status: TodoItem['status']) => {
    if (status === 'in_progress') return t('chat.todo.in_progress', 'In progress');
    if (status === 'done') return t('chat.todo.done', 'Done');
    return t('chat.todo.pending', 'Pending');
  };
  const statusTone = (status: TodoItem['status']): SemanticStatus => {
    if (status === 'in_progress') return 'running';
    if (status === 'done') return 'success';
    return 'neutral';
  };

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => onCollapsedChange(false)}
        className="mb-2 inline-flex h-7 max-w-full items-center gap-2 rounded-full border border-edge-structural bg-surface-raised px-3 text-xs text-muted-foreground transition-colors duration-feedback hover:bg-muted/40 hover:text-foreground"
        aria-label={t('chat.todo.expand', 'Show tasks')}
        title={t('chat.todo.expand', 'Show tasks')}
      >
        <ListTodo className="h-3.5 w-3.5" />
        <span className="font-medium text-foreground">{t('chat.todo.title', 'Tasks')}</span>
        <span>
          {active > 0
            ? `${t('chat.todo.active', '{{count}} active', { count: active })} · `
            : ''}
          {t('chat.todo.left', '{{count}} left', { count: left })}
        </span>
        <ChevronUp className="h-3.5 w-3.5" />
      </button>
    );
  }

  return (
    <section
      className="mb-2 w-full max-w-[460px] rounded-lg border border-edge-structural bg-surface-raised text-sm"
      aria-label={t('chat.todo.title', 'Tasks')}
      data-role="chat-todo-dock"
    >
      <div className="flex h-9 items-center justify-between gap-3 border-b px-3">
        <div className="flex min-w-0 items-center gap-2">
          <ListTodo className="h-4 w-4 text-muted-foreground" />
          <span className="font-medium">{t('chat.todo.title', 'Tasks')}</span>
          <span className="truncate text-xs text-muted-foreground">
            {t('chat.todo.done_count', '{{done}}/{{total}} done', { done, total })}
          </span>
        </div>
        <button
          type="button"
          onClick={() => onCollapsedChange(true)}
          className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
          aria-label={t('chat.todo.collapse', 'Collapse tasks')}
          title={t('chat.todo.collapse', 'Collapse tasks')}
        >
          <ChevronDown className="h-4 w-4" />
        </button>
      </div>
      <div className="max-h-44 overflow-y-auto px-2 py-2">
        {items.map((item) => (
          <div
            key={item.id}
            className={cn(
              'flex items-start gap-2 rounded-md px-2 py-1.5',
              item.status === 'done' ? 'text-muted-foreground' : 'text-foreground',
            )}
          >
            <StatusDot className="mt-1.5" status={statusTone(item.status)} />
            <div className="min-w-0 flex-1">
              <div className={cn('break-words leading-5', item.status === 'done' && 'line-through')}>
                {item.text}
              </div>
              <div className="mt-0.5 text-xs leading-4 text-muted-foreground">
                {statusLabel(item.status)}
              </div>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
