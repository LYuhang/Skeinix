/**
 * Light / dark / system theme toggle.
 *
 * Thin wrapper over `next-themes` — `<ThemeProvider attribute="class">` in
 * `app/providers.tsx` flips the `class` on `<html>` between
 * `light` / `dark`, which is what Tailwind's `darkMode: ['class']` and the
 * shadcn CSS variables (`:root` vs `:root.dark` in `src/index.css`) react to.
 *
 * The trigger icon reflects the *user's chosen* mode, not the resolved one:
 *   - 'system' → Monitor (we don't claim a side; we follow prefers-color-scheme)
 *   - 'light'  → Sun
 *   - 'dark'   → Moon
 *
 * Note on first render: `useTheme()` returns `theme: undefined` for one
 * frame before the provider hydrates. We render the system icon as the
 * neutral fallback — acceptable one-frame flicker; no `mounted` guard
 * needed for our CSR-only Vite setup.
 */
import { useTheme } from 'next-themes';
import { Sun, Moon, Monitor } from 'lucide-react';
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu';
import { Button } from '@/components/ui/button';
import { useTranslation } from 'react-i18next';

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const { t } = useTranslation();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          aria-label={t('toggle_theme', 'Toggle theme')}
          data-action="toggle-theme"
        >
          {theme === 'dark' ? (
            <Moon className="h-4 w-4" />
          ) : theme === 'light' ? (
            <Sun className="h-4 w-4" />
          ) : (
            <Monitor className="h-4 w-4" />
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuItem onClick={() => setTheme('light')}>
          {t('theme_light', 'Light')}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme('dark')}>
          {t('theme_dark', 'Dark')}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme('system')}>
          {t('theme_system', 'System')}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
