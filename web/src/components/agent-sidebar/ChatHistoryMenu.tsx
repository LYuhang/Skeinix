/**
 * Header History affordance: a DropdownMenu of the workflow's chat sessions.
 * `useChatSessions` is called at the top level (not inside the menu content)
 * so it fetches on mount, not on first open (radix mounts content lazily).
 */
import { useEffect, useState } from 'react';
import { History } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import { useChatSessions } from '@/lib/api/queries/chats';

export interface ChatHistoryMenuProps {
  wfId: string;
  activeChatId: string | null;
  onSelect: (chatId: string) => void;
  onIntent?: (chatId: string) => void;
  surface?: 'chat' | 'browser';
  /** Close portalled menu content while its parent panel is CSS-hidden. */
  active?: boolean;
}

export function ChatHistoryMenu({
  wfId,
  activeChatId,
  onSelect,
  onIntent,
  surface = 'chat',
  active = true,
}: ChatHistoryMenuProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const sessions = useChatSessions(wfId, surface);
  const items = (sessions.data?.items ?? []) as Array<{
    chat_id: string;
    chat_context?: string | null;
  }>;

  useEffect(() => {
    // Closing synchronously prevents portalled menu content remaining visible
    // when its parent panel is hidden.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (!active) setOpen(false);
  }, [active]);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('chat_history', 'Chat History')}
          data-action="agent-sidebar-history"
          disabled={!active}
        >
          <History className="h-4 w-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        collisionPadding={8}
        className="max-h-[min(24rem,calc(100vh-4rem))] w-[min(20rem,calc(100vw-1rem))] overflow-y-auto"
      >
        {sessions.isLoading ? (
          <div className="space-y-1 p-1">
            <Skeleton className="h-6 w-full" />
            <Skeleton className="h-6 w-full" />
          </div>
        ) : items.length ? (
          items.map((s) => {
            const label = s.chat_context || s.chat_id.slice(0, 8);
            return (
              <DropdownMenuItem
                key={s.chat_id}
                data-chat-id={s.chat_id}
                onSelect={() => onSelect(s.chat_id)}
                onPointerEnter={() => onIntent?.(s.chat_id)}
                onFocus={() => onIntent?.(s.chat_id)}
                className={s.chat_id === activeChatId ? 'min-w-0 bg-accent text-accent-foreground' : 'min-w-0'}
                title={label}
              >
                <span className="min-w-0 flex-1 truncate whitespace-nowrap">{label}</span>
              </DropdownMenuItem>
            );
          })
        ) : (
          <div className="px-2 py-1.5 text-xs text-muted-foreground">{t('no_chats', 'No chats yet.')}</div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
