import { type ComponentProps, useEffect } from 'react';
import { useTheme } from 'next-themes';

import {
  APP_ICON_DARK_SRC,
  APP_ICON_LIGHT_SRC,
  appIconSrc,
} from '@/lib/branding';

type AppIconProps = Omit<ComponentProps<'img'>, 'src'>;

function initialDocumentTheme(): 'light' | 'dark' {
  if (typeof document !== 'undefined' && document.documentElement.classList.contains('dark')) {
    return 'dark';
  }
  return 'light';
}

/** Theme-aware product icon used by every in-app brand surface. */
export function AppIcon({ alt = '', ...props }: AppIconProps) {
  const { resolvedTheme } = useTheme();
  const theme = resolvedTheme === 'light' || resolvedTheme === 'dark'
    ? resolvedTheme
    : initialDocumentTheme();

  // Warm the alternate colorway once the current icon is visible so changing
  // Theme never flashes an empty image, while the first paint still downloads
  // only the colorway it needs.
  useEffect(() => {
    const alternate = new Image();
    alternate.src = theme === 'dark' ? APP_ICON_LIGHT_SRC : APP_ICON_DARK_SRC;
  }, [theme]);

  return <img {...props} src={appIconSrc(theme)} alt={alt} />;
}
