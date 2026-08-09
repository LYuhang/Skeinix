import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Building2, Search, Trash2, UserRound, UsersRound } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  changeResourceBinding,
  listOrganizationGroups,
  listOrganizationMembers,
  listResourceBindings,
  type DirectBinding,
  type ShareableResourceKind,
  type ShareRelation,
} from '@/lib/api/organizations';
import { useAuthStore } from '@/stores/auth';

export interface ResourceShareDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  resourceKind: ShareableResourceKind;
  resourceId: string;
  resourceName: string;
  effectiveRole?: string | null;
  accessSource?: string;
}

type TargetKind = 'user' | 'group' | 'organization';

export function ResourceShareDialog({
  open,
  onOpenChange,
  resourceKind,
  resourceId,
  resourceName,
  effectiveRole,
  accessSource,
}: ResourceShareDialogProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const organizationId = useAuthStore((state) => state.user?.tenant_id ?? '');
  const bindingKey = ['resource-access', organizationId, resourceKind, resourceId] as const;
  const bindings = useQuery({
    queryKey: bindingKey,
    queryFn: () => listResourceBindings(resourceKind, resourceId),
    enabled: open && Boolean(resourceId),
    retry: false,
  });
  const members = useQuery({
    queryKey: ['organization-members', organizationId],
    queryFn: () => listOrganizationMembers(organizationId),
    enabled: open && Boolean(organizationId),
  });
  const groups = useQuery({
    queryKey: ['organization-groups', organizationId],
    queryFn: () => listOrganizationGroups(organizationId),
    enabled: open && Boolean(organizationId),
  });
  const [targetKind, setTargetKind] = useState<TargetKind>('user');
  const [targetId, setTargetId] = useState('');
  const [relation, setRelation] = useState<ShareRelation>('viewer');
  const [search, setSearch] = useState('');
  const relationOptions: readonly ShareRelation[] = [
    'viewer',
    'editor',
    'operator',
    'manager',
  ];
  const selectedRelation = relationOptions.includes(relation) ? relation : 'viewer';
  const options = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (targetKind === 'organization') {
      return [{ id: organizationId, label: t('share.everyone', 'Everyone in this workspace') }];
    }
    if (targetKind === 'group') {
      return (groups.data ?? [])
        .map((item) => ({ id: item.group_id, label: item.name }))
        .filter((item) => !needle || item.label.toLowerCase().includes(needle));
    }
    return (members.data ?? [])
      .filter((item) => item.status === 'active')
      .map((item) => ({ id: item.user_id, label: item.display_name || item.email, detail: item.email }))
      .filter((item) => !needle || `${item.label} ${item.detail}`.toLowerCase().includes(needle));
  }, [groups.data, members.data, organizationId, search, t, targetKind]);

  const mutation = useMutation({
    mutationFn: ({ binding, desiredPresent }: {
      binding: Omit<DirectBinding, 'source'>;
      desiredPresent: boolean;
    }) => changeResourceBinding(resourceKind, resourceId, binding, desiredPresent),
    onSuccess: async (_, variables) => {
      await queryClient.invalidateQueries({ queryKey: bindingKey });
      if (variables.desiredPresent) {
        setTargetId('');
        setSearch('');
        toast.success(t('share.granted', 'Access granted'));
      } else {
        toast.success(t('share.revoked', 'Access revoked'));
      }
    },
    onError: (reason) => toast.error(
      reason instanceof Error ? reason.message : t('share.changeFailed', 'Access change failed'),
    ),
  });

  const bindingFor = (kind: TargetKind, id: string, role: ShareRelation) => ({
    relation: role,
    subject_type: kind,
    subject_id: id,
    subject_relation: kind === 'group' ? 'member' as const : kind === 'organization' ? 'member' as const : null,
  });
  const labelFor = (binding: DirectBinding) => {
    if (binding.subject_type === 'organization') {
      return t('share.everyone', 'Everyone in this workspace');
    }
    if (binding.subject_type === 'group') {
      return groups.data?.find((item) => item.group_id === binding.subject_id)?.name ?? binding.subject_id;
    }
    return members.data?.find((item) => item.user_id === binding.subject_id)?.display_name
      || members.data?.find((item) => item.user_id === binding.subject_id)?.email
      || binding.subject_id;
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{t('share.resourceTitle', 'Share resource')}</DialogTitle>
          <DialogDescription>
            {t('share.description', 'Manage direct access to “{{name}}”.', { name: resourceName })}
          </DialogDescription>
        </DialogHeader>

        <div className="rounded-lg border border-edge-subtle bg-muted/35 px-3 py-2.5 text-sm">
          <p className="font-medium">{t('share.effectiveAccess', 'Your effective access')}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
            {effectiveRole ?? t('share.customAccess', 'custom access')} · {accessSource ?? 'computed'}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {t('share.inheritedHint', 'Inherited access is read-only here; change it in the source group or organization.')}
          </p>
        </div>

        <section className="grid gap-3">
          <p className="text-sm font-medium">{t('share.addDirect', 'Add direct access')}</p>
          <div className="grid grid-cols-[130px_minmax(0,1fr)_120px] gap-2">
            <Select value={targetKind} onValueChange={(value) => {
              const kind = value as TargetKind;
              setTargetKind(kind);
              setTargetId(kind === 'organization' ? organizationId : '');
              if (kind === 'organization') setRelation('viewer');
            }}>
              <SelectTrigger aria-label={t('share.targetType', 'Target type')}><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="user">{t('share.user', 'User')}</SelectItem>
                <SelectItem value="group">{t('share.group', 'Group')}</SelectItem>
                <SelectItem value="organization">{t('share.workspace', 'Workspace')}</SelectItem>
              </SelectContent>
            </Select>
            <Select value={targetId} onValueChange={setTargetId}>
              <SelectTrigger aria-label={t('share.target', 'Access target')}><SelectValue placeholder={t('share.chooseTarget', 'Choose…')} /></SelectTrigger>
              <SelectContent>
                {options.map((option) => <SelectItem key={option.id} value={option.id}>{option.label}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={selectedRelation} disabled={targetKind === 'organization'} onValueChange={(value) => setRelation(value as ShareRelation)}>
              <SelectTrigger aria-label={t('share.role', 'Access role')}><SelectValue /></SelectTrigger>
              <SelectContent>
                {relationOptions.map((role) => (
                  <SelectItem key={role} value={role}>
                    {t(`share.role.${role}`, role)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {targetKind !== 'organization' ? (
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input value={search} onChange={(event) => setSearch(event.target.value)} className="pl-8" placeholder={t('share.search', 'Filter users or groups…')} />
            </div>
          ) : null}
          <div className="flex justify-end">
            <Button
              disabled={!targetId || mutation.isPending}
              onClick={() => mutation.mutate({
                binding: bindingFor(targetKind, targetId, selectedRelation),
                desiredPresent: true,
              })}
            >
              {t('share.grant', 'Grant access')}
            </Button>
          </div>
        </section>

        <section className="grid gap-2">
          <p className="text-sm font-medium">{t('share.directAccess', 'Direct access')}</p>
          <div className="max-h-56 divide-y overflow-auto rounded-lg border border-edge-subtle">
            {bindings.isLoading ? (
              <p className="p-4 text-sm text-muted-foreground">{t('common.loading', 'Loading…')}</p>
            ) : bindings.isError ? (
              <p className="p-4 text-sm text-destructive">{t('share.loadFailed', 'Could not load direct access.')}</p>
            ) : bindings.data?.length ? bindings.data.map((binding) => (
              <div key={`${binding.relation}:${binding.subject_type}:${binding.subject_id}:${binding.subject_relation ?? ''}`} className="flex items-center gap-3 px-3 py-2.5">
                {binding.subject_type === 'group' ? <UsersRound className="size-4 text-muted-foreground" /> : binding.subject_type === 'organization' ? <Building2 className="size-4 text-muted-foreground" /> : <UserRound className="size-4 text-muted-foreground" />}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{labelFor(binding)}</p>
                  <p className="text-xs text-muted-foreground">{binding.subject_type} · {binding.relation} · direct</p>
                </div>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  disabled={mutation.isPending}
                  aria-label={t('share.revoke', 'Revoke access')}
                  onClick={() => mutation.mutate({
                    binding: {
                      relation: binding.relation,
                      subject_type: binding.subject_type,
                      subject_id: binding.subject_id,
                      subject_relation: binding.subject_relation,
                    },
                    desiredPresent: false,
                  })}
                >
                  <Trash2 />
                </Button>
              </div>
            )) : (
              <p className="p-4 text-sm text-muted-foreground">{t('share.noDirectAccess', 'No direct access entries.')}</p>
            )}
          </div>
        </section>
      </DialogContent>
    </Dialog>
  );
}
