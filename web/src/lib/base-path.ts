/** Runtime deployment coordinates shared by Router, HTTP, SSE, and media. */
export interface VibeCanvasRuntimeConfig {
  /** Path only, for example `/studio`; `/` means an origin-root deployment. */
  basePath?: string;
  /** Optional same-origin path or absolute API origin. */
  apiBase?: string;
  /** Automatically discovered by index.html; explicit config still wins. */
  inferredBasePath?: string;
}

function runtimeConfig(): VibeCanvasRuntimeConfig | undefined {
  if (typeof window === 'undefined') return undefined;
  return window.__VIBECANVAS_RUNTIME_CONFIG__;
}

/** Normalize a configured mount path for React Router and same-origin URLs. */
export function normalizeBasePath(value: string): string {
  const raw = value.trim();
  if (!raw || raw === '/') return '';
  try {
    const pathname = /^https?:\/\//i.test(raw) ? new URL(raw).pathname : raw;
    const withoutQuery = pathname.split(/[?#]/, 1)[0] ?? '';
    const normalized = `/${withoutQuery.replace(/^\/+|\/+$/g, '')}`;
    return normalized === '/' ? '' : normalized;
  } catch {
    return '';
  }
}

/**
 * Derive the mount point from any emitted module URL. Every production module
 * lives below `<mount>/assets/`, so this works for an arbitrary reverse-proxy
 * prefix without knowing its domain, token format, or path depth.
 */
export function basePathFromModuleUrl(moduleUrl: string): string {
  try {
    const pathname = new URL(moduleUrl).pathname;
    const marker = pathname.lastIndexOf('/assets/');
    return marker >= 0 ? normalizeBasePath(pathname.slice(0, marker)) : '';
  } catch {
    return '';
  }
}

/**
 * Resolve the application mount path in descending precedence:
 *
 * 1. host-injected runtime config (no rebuild required),
 * 2. `VITE_APP_BASE_PATH` for a fixed build-time deployment,
 * 3. entry-page inference before assets load,
 * 4. fallback inference from the currently loaded module URL.
 */
export function getBasePath(): string {
  const injected = runtimeConfig()?.basePath?.trim();
  if (injected) return normalizeBasePath(injected);
  const built = import.meta.env.VITE_APP_BASE_PATH?.trim();
  if (built) return normalizeBasePath(built);
  const inferred = runtimeConfig()?.inferredBasePath?.trim();
  if (inferred) return normalizeBasePath(inferred);
  return basePathFromModuleUrl(import.meta.url);
}

/** One API-origin resolver for fetch, SSE, signed media, and browser surfaces. */
export function getApiBase(): string {
  const configured = runtimeConfig()?.apiBase?.trim() || import.meta.env.VITE_API_BASE?.trim();
  if (!configured) return getBasePath();
  if (/^https?:\/\//i.test(configured)) return configured.replace(/\/+$/, '');
  return normalizeBasePath(configured);
}

/**
 * Resolve one backend-owned path against the runtime API mount.
 *
 * Backend payloads intentionally return origin-style paths such as
 * `/api/v1/vfs/resources/<capability>/`. `new URL(path, base)` is not suitable
 * here: a leading slash discards an opaque reverse-proxy prefix. Joining the
 * two coordinates explicitly keeps the same build valid at `/`, below a
 * temporary workspace path, and behind an absolute external API base.
 */
export function resolveApiUrl(path: string): string {
  if (/^https?:\/\//i.test(path)) return path;
  const apiBase = getApiBase().replace(/\/+$/, '');
  const suffix = `/${path.replace(/^\/+/, '')}`;
  if (/^https?:\/\//i.test(apiBase)) return `${apiBase}${suffix}`;
  if (typeof window === 'undefined') return `${apiBase}${suffix}`;
  return `${window.location.origin}${apiBase}${suffix}`;
}
