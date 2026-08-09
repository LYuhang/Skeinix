/**
 * Root-level context providers.
 *
 * Composes:
 *   - QueryClientProvider — server-state cache. The `QueryClient` itself
 *     lives in `./query-client.ts` as a module-scope singleton so non-React
 *     consumers (the SSE signal router) can import it without pulling in
 *     the provider tree, and so this file stays component-only for Vite
 *     Fast Refresh.
 *   - ThemeProvider (next-themes) — system/dark/light class on <html>.
 *   - TooltipProvider — shared 300ms delay for all tooltips.
 *   - Toaster (sonner) — top-right toast surface.
 *
 * Toast layout (Bug B)
 * --------------------
 * sonner's default toast is a single flex ROW: `[content] [action] [cancel]`,
 * where the buttons are `flex-shrink: 0` and the content column inherits the
 * flex default `min-width: auto`. With the actionable agent-conflict toast —
 * a long message PLUS two long-labelled buttons ("Load agent version (discard
 * my edits)" + "Keep mine") — the unshrinkable buttons eat the fixed-width
 * row, the content column collapses toward 0, and the title wraps ONE
 * CHARACTER PER LINE. We fix it via `toastOptions.classNames` (real DOM nodes,
 * so Tailwind utilities apply):
 *   - widen the surface a little (`--width`);
 *   - stack the toast vertically so the buttons drop BELOW the message
 *     instead of competing with it for the row;
 *   - give the content column `min-w-0` + word-wrap so the message wraps by
 *     WORD, and let the action row lay its two buttons out cleanly.
 * Normal single-message toasts (success/error) have no buttons, so the column
 * stack is a no-op for them — they still render as a tidy one-line card.
 */
import { type CSSProperties, type ReactNode, useEffect } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider, useTheme } from 'next-themes';
import { Toaster } from 'sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { queryClient } from '@/app/query-client';
import { appIconSrc } from '@/lib/branding';

export interface ProvidersProps {
  children: ReactNode;
}

function DocumentThemeMetadata() {
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
    if (meta) meta.content = resolvedTheme === 'dark' ? '#17191e' : '#f1f3f6';
    const favicon = document.querySelector<HTMLLinkElement>('link[data-app-favicon]');
    if (favicon) favicon.href = appIconSrc(resolvedTheme);
  }, [resolvedTheme]);

  return null;
}

export function Providers({ children }: ProvidersProps) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
        <DocumentThemeMetadata />
        <TooltipProvider delayDuration={300}>
          {children}
          <Toaster
            position="top-right"
            visibleToasts={3}
            style={{ '--width': '360px' } as CSSProperties}
            toastOptions={{
              classNames: {
                // Column stack: the action row drops BELOW the message instead
                // of squeezing the content column to a per-character sliver.
                toast: 'flex flex-col items-stretch gap-2 border-edge-structural bg-surface-raised text-foreground shadow-popover',
                // `min-w-0` lifts the flex `min-width: auto` floor so the text
                // can shrink + wrap by WORD; `break-words` is the belt-and-
                // braces against any single long token.
                content: 'min-w-0 break-words',
                title: 'whitespace-normal break-words',
                description: 'whitespace-normal break-words',
                // Lay the two buttons out side-by-side at the row's end.
                actionButton: 'shrink-0',
                cancelButton: 'shrink-0',
              },
            }}
          />
        </TooltipProvider>
      </ThemeProvider>
    </QueryClientProvider>
  );
}
