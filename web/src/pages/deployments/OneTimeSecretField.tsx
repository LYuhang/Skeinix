import { useEffect, useState } from 'react';
import { Copy, Eye, EyeOff } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

export function OneTimeSecretField({
  value,
  label,
  testId = 'one-shot-secret',
}: {
  value: string;
  label: string;
  testId?: string;
}) {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    // A newly issued secret must never inherit the previous value's revealed
    // state, even briefly.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setVisible(false);
  }, [value]);

  const copySecret = () => {
    if (!navigator.clipboard?.writeText) {
      toast.error(t('deployments.create.copyFailed', 'Copy failed — copy manually'));
      return;
    }
    navigator.clipboard.writeText(value).then(
      () => toast.success(t('deployments.create.copied', 'Copied')),
      () => toast.error(t('deployments.create.copyFailed', 'Copy failed — copy manually')),
    );
  };

  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="flex items-center gap-2">
        <div className="relative min-w-0 flex-1">
          <Input
            data-testid={testId}
            type={visible ? 'text' : 'password'}
            value={value}
            readOnly
            autoComplete="off"
            spellCheck={false}
            className="pr-10 font-mono text-xs"
            aria-label={label}
          />
          <button
            type="button"
            className="absolute right-1.5 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-md text-muted-foreground hover:bg-surface-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => setVisible((current) => !current)}
            aria-label={visible
              ? t('deployments.secret.hide', 'Hide secret')
              : t('deployments.secret.show', 'Show secret')}
            aria-pressed={visible}
          >
            {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
          </button>
        </div>
        <Button type="button" variant="outline" size="sm" onClick={copySecret}>
          <Copy className="size-4" />
          {t('deployments.create.copy', 'Copy')}
        </Button>
      </div>
    </div>
  );
}
