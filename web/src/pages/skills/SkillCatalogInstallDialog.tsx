import { BookOpenText, Check, Files } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { useInstallSkillCatalogItem } from '@/lib/api/queries/skills';
import type { Skill, SkillCatalogItem } from '@/lib/api/skills';

export function SkillCatalogInstallDialog({
  open,
  onOpenChange,
  skill,
  onInstalled,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  skill: SkillCatalogItem;
  onInstalled: (installed: Skill) => void;
}) {
  const { t } = useTranslation();
  const installMutation = useInstallSkillCatalogItem();

  const install = async () => {
    try {
      const installed = await installMutation.mutateAsync({ source: skill.source, sourceId: skill.source_id });
      toast.success(t('skills.catalog.installed', { name: skill.name, defaultValue: '{{name}} Installed' }));
      onOpenChange(false);
      onInstalled(installed);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{t('skills.catalog.install_title', { name: skill.name, defaultValue: 'Install {{name}}' })}</DialogTitle>
          <DialogDescription>{t('skills.catalog.install_desc', 'Install this instruction package for agents in your workspace.')}</DialogDescription>
        </DialogHeader>
        <div className="flex items-center gap-3 border-y py-4">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
            <BookOpenText className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate font-medium">{skill.name}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">{skill.source_label}</div>
          </div>
        </div>
        <div className="space-y-2 text-sm text-muted-foreground">
          <div className="flex items-start gap-2"><Files className="mt-0.5 h-4 w-4 shrink-0 text-state-success" />{t('skills.catalog.install_files', { count: skill.files.length, defaultValue: 'Installs {{count}} package files.' })}</div>
          <div className="flex items-start gap-2"><Check className="mt-0.5 h-4 w-4 shrink-0 text-state-success" />{t('skills.catalog.install_validate', 'Validates SKILL.md before the package is saved.')}</div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={installMutation.isPending}>{t('skills.cancel', 'Cancel')}</Button>
          <Button onClick={() => void install()} disabled={installMutation.isPending}>{installMutation.isPending ? t('skills.catalog.installing', 'Installing…') : t('skills.catalog.install', 'Install')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
