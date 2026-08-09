/**
 * Small keyboard-focusable copy-to-clipboard button.
 *
 * Fail-soft: a clipboard write failure is swallowed (no crash, no toast) —
 * copy is a convenience, not a critical path. Shows a transient "copied"
 * state for ~1.2s.
 */
import { useCallback, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { cn } from '@/lib/utils';

export interface CopyButtonProps {
  value: string;
  label?: string;
  className?: string;
}

export function CopyButton({ value, label, className }: CopyButtonProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const onCopy = useCallback(() => {
    const done = () => {
      setCopied(true);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setCopied(false), 1200);
    };
    try {
      const cb = navigator.clipboard;
      if (cb?.writeText) {
        cb.writeText(value).then(done, () => {
          /* swallow — copy is best-effort */
        });
      }
    } catch {
      /* swallow — copy is best-effort */
    }
  }, [value]);

  return (
    <button
      type="button"
      onClick={onCopy}
      title={copied ? t('tool.copied') : (label || t('tool.copy'))}
      aria-label={copied ? t('tool.copied') : (label || t('tool.copy'))}
      data-action="tool-copy"
      className={cn(
        'inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium',
        'text-muted-foreground hover:bg-accent/60 hover:text-foreground',
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
        className,
      )}
    >
      {copied ? (
        <Check className="h-3 w-3 text-state-success" />
      ) : (
        <Copy className="h-3 w-3" />
      )}
      <span>{copied ? t('tool.copied') : (label || t('tool.copy'))}</span>
    </button>
  );
}
