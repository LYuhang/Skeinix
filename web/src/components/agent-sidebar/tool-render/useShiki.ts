/**
 * Minimal, streaming-safe Shiki highlighter hook.
 *
 * Wraps Shiki's async `codeToHtml` (full bundle — lazy-loads only the
 * requested grammar + theme on demand) and returns the highlighted HTML
 * string, or `null` until it resolves / on any failure. Callers render a
 * plain `<pre>` fallback while `null`, so render is NEVER blocked while the
 * highlighter (WASM + grammar) loads.
 *
 * Why not `react-shiki`: its component chunk side-effect-imports a
 * `style.css` containing `@layer base`, which Tailwind v3's PostCSS pass
 * rejects when processed standalone (no `@tailwind base` in scope) and
 * breaks `vite build`. A direct Shiki call avoids that CSS entirely; the
 * output HTML carries inline theme colors, so no extra stylesheet is needed.
 *
 * The HTML is produced by Shiki from the (escaped) source code — it is not
 * arbitrary user HTML — so `dangerouslySetInnerHTML` is safe here.
 */
import { useEffect, useState } from 'react';

export function useShiki(code: string, lang: string): string | null {
  const key = `${lang}\u0000${code}`;
  const [result, setResult] = useState<{ key: string; html: string | null } | null>(null);

  useEffect(() => {
    let cancelled = false;
    // Dynamic import keeps Shiki + its WASM out of the initial bundle and
    // off the synchronous render path.
    import('shiki')
      .then(({ codeToHtml }) =>
        codeToHtml(code, {
          lang,
          themes: { light: 'github-light', dark: 'github-dark' },
          defaultColor: false,
        }),
      )
      .then((out) => {
        if (!cancelled) setResult({ key, html: out });
      })
      .catch(() => {
        // Fail-soft: leave html null → caller shows the plain <pre> fallback.
        if (!cancelled) setResult({ key, html: null });
      });
    return () => {
      cancelled = true;
    };
  }, [code, key, lang]);

  return result?.key === key ? result.html : null;
}
