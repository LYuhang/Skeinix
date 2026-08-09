import { useEffect, useRef, useState } from 'react';
import { Check, Copy } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Button, type ButtonProps } from '@/components/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

export interface CopyButtonProps extends Omit<ButtonProps, 'onClick' | 'children'> {
  value: string;
  label?: string;
  copiedLabel?: string;
  resetAfterMs?: number;
  showLabel?: boolean;
  onCopyError?: (error: unknown) => void;
}

export function CopyButton({
  value,
  label,
  copiedLabel,
  resetAfterMs = 3000,
  showLabel = false,
  className,
  onCopyError,
  ...props
}: CopyButtonProps) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const timeoutRef = useRef<number | null>(null);
  const copyLabel = label ?? t('copy');
  const successLabel = copiedLabel ?? t('copied');

  useEffect(() => () => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
  }, []);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
      timeoutRef.current = window.setTimeout(() => setCopied(false), resetAfterMs);
    } catch (error) {
      onCopyError?.(error);
    }
  };

  const currentLabel = copied ? successLabel : copyLabel;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          type="button"
          variant="quiet"
          size={showLabel ? 'sm' : 'icon-sm'}
          className={cn(copied && 'text-state-success', className)}
          aria-label={currentLabel}
          onClick={() => void handleCopy()}
          {...props}
        >
          {copied ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
          {showLabel ? <span>{currentLabel}</span> : null}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{currentLabel}</TooltipContent>
    </Tooltip>
  );
}
