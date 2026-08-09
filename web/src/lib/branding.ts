export const APP_ICON_LIGHT_SRC = 'branding/icon-light.png';
export const APP_ICON_DARK_SRC = 'branding/icon-dark.png';

export function appIconSrc(theme: string | undefined): string {
  return theme === 'dark' ? APP_ICON_DARK_SRC : APP_ICON_LIGHT_SRC;
}
