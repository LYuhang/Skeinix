import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus, Search } from 'lucide-react';
import { Link } from 'react-router';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import { ManagementPageShell } from '@/components/layout/management-page-shell';
import { ActionableError } from '@/components/presentation/ActionableError';
import { CompactEmptyState } from '@/components/presentation/CompactEmptyState';
import { ResourceIcon } from '@/components/presentation/ResourceIcon';
import { AsyncState } from '@/components/ui/async-state';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { StatusBadge, type SemanticStatus } from '@/components/ui/status';
import { Textarea } from '@/components/ui/textarea';
import { createKb, listKbs, type KbListItem } from '@/lib/api/kb';
import { useFormatDateTime } from '@/lib/timezone';

const knowledgeKey = ['knowledge-bases'] as const;
type KnowledgeHealth = 'all' | 'ready' | 'indexing' | 'attention' | 'empty';
type KnowledgeSort = 'updated' | 'created' | 'name';

function knowledgeStatus(item: KbListItem): {
  value: Exclude<KnowledgeHealth, 'all'>;
  tone: SemanticStatus;
  label: string;
} {
  if (item.failed_count > 0) return { value: 'attention', tone: 'warning', label: 'Needs attention' };
  if (item.pending_count + item.indexing_count > 0) return { value: 'indexing', tone: 'running', label: 'Indexing' };
  if (item.indexed_count > 0) return { value: 'ready', tone: 'success', label: 'Ready' };
  return { value: 'empty', tone: 'neutral', label: 'Empty' };
}

function errorState(error: unknown): 'permission' | 'error' {
  const message = error instanceof Error ? error.message : String(error ?? '');
  return /(?:\b403\b|forbidden|permission)/i.test(message) ? 'permission' : 'error';
}

export function KnowledgeListPage() {
  const { t } = useTranslation();
  const formatTime = useFormatDateTime();
  const client = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [health, setHealth] = useState<KnowledgeHealth>('all');
  const [sort, setSort] = useState<KnowledgeSort>('updated');
  const knowledge = useQuery({ queryKey: knowledgeKey, queryFn: listKbs });
  const create = useMutation({
    mutationFn: () => createKb({ name: name.trim(), description: description.trim() || undefined }),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: knowledgeKey });
      setCreateOpen(false);
      setName('');
      setDescription('');
      toast.success(t('knowledge.created', 'Knowledge base created'));
    },
    onError: (reason) => toast.error(reason instanceof Error ? reason.message : String(reason)),
  });
  const filtered = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return (knowledge.data ?? [])
      .filter((item) => (
        !normalized
        || `${item.name} ${item.description ?? ''}`.toLocaleLowerCase().includes(normalized)
      ))
      .filter((item) => health === 'all' || knowledgeStatus(item).value === health)
      .sort((left, right) => {
        if (sort === 'name') return left.name.localeCompare(right.name);
        const leftDate = Date.parse(sort === 'created' ? left.created_at : left.latest_updated_at);
        const rightDate = Date.parse(sort === 'created' ? right.created_at : right.latest_updated_at);
        return rightDate - leftDate;
      });
  }, [health, knowledge.data, query, sort]);
  const hasActiveFilters = Boolean(query.trim()) || health !== 'all';

  return (
    <ManagementPageShell
      resourceKind="knowledge"
      title={t('knowledge.title', 'Knowledge')}
      description={t('knowledge.description', 'Curate sources the Agent can retrieve through the explicit /knowledge capability.')}
      actions={<Button onClick={() => setCreateOpen(true)}><Plus className="h-4 w-4" />{t('knowledge.create', 'New knowledge base')}</Button>}
    >
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-64 flex-1 sm:max-w-md">
          <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-content-tertiary" />
          <Input value={query} onChange={(event) => setQuery(event.target.value)} className="pl-9" placeholder={t('knowledge.searchList', 'Search knowledge bases')} />
        </div>
        <Select value={health} onValueChange={(value) => setHealth(value as KnowledgeHealth)}>
          <SelectTrigger className="w-44" aria-label={t('knowledge.filterHealth', 'Filter by status')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t('knowledge.filter.all', 'All statuses')}</SelectItem>
            <SelectItem value="ready">{t('knowledge.filter.ready', 'Ready')}</SelectItem>
            <SelectItem value="indexing">{t('knowledge.filter.indexing', 'Indexing')}</SelectItem>
            <SelectItem value="attention">{t('knowledge.filter.attention', 'Needs attention')}</SelectItem>
            <SelectItem value="empty">{t('knowledge.filter.empty', 'Empty')}</SelectItem>
          </SelectContent>
        </Select>
        <Select value={sort} onValueChange={(value) => setSort(value as KnowledgeSort)}>
          <SelectTrigger className="w-40" aria-label={t('knowledge.sort', 'Sort knowledge bases')}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="updated">{t('knowledge.sort.updated', 'Recently updated')}</SelectItem>
            <SelectItem value="created">{t('knowledge.sort.created', 'Newest created')}</SelectItem>
            <SelectItem value="name">{t('knowledge.sort.name', 'Name')}</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {knowledge.isLoading ? <AsyncState kind="loading" title={t('knowledge.loading', 'Loading knowledge bases…')} /> : null}
      {knowledge.isError && errorState(knowledge.error) === 'permission' ? (
        <AsyncState
          kind="permission"
          title={t('knowledge.forbidden', 'You do not have access to Knowledge')}
        />
      ) : null}
      {knowledge.isError && errorState(knowledge.error) === 'error' ? (
        <ActionableError
          title={t('knowledge.loadFailed', 'Could not load knowledge bases')}
          description={t('knowledge.loadFailedHint', 'Check the connection and try loading the list again.')}
          actionLabel={t('retry', 'Retry')}
          onAction={() => void knowledge.refetch()}
          technicalDetails={knowledge.error instanceof Error ? knowledge.error.message : String(knowledge.error)}
          technicalDetailsLabel={t('common.technicalDetails', 'Technical details')}
        />
      ) : null}
      {!knowledge.isLoading && !knowledge.isError && !filtered.length ? (
        <CompactEmptyState
          title={hasActiveFilters ? t('knowledge.noMatch', 'No matching knowledge bases') : t('knowledge.empty', 'No knowledge bases yet')}
          description={hasActiveFilters ? t('knowledge.noMatchHint', 'Try a different name or clear the search.') : t('knowledge.emptyHint', 'Create one, upload source files, then test retrieval before enabling /knowledge in Chat.')}
          actionLabel={!hasActiveFilters ? t('knowledge.create', 'New knowledge base') : undefined}
          onAction={!hasActiveFilters ? () => setCreateOpen(true) : undefined}
        />
      ) : null}
      {filtered.length ? (
        <div className="divide-y divide-edge-subtle border-y border-edge-subtle">
          {filtered.map((item) => (
            <Link key={item.id} to={`/knowledge/${item.id}`} className="interactive-row group flex min-h-20 items-center gap-4 px-3 py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-focus">
              <ResourceIcon kind="knowledge" size="lg" className="size-10 rounded-lg" />
              <span className="min-w-0 flex-1">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate text-sm font-medium text-content-primary">{item.name}</span>
                  <StatusBadge status={knowledgeStatus(item).tone}>
                    {t(`knowledge.status.${knowledgeStatus(item).value}`, knowledgeStatus(item).label)}
                  </StatusBadge>
                </span>
                <span className="mt-0.5 block truncate text-sm text-content-secondary">{item.description || t('knowledge.noDescription', 'No description')}</span>
                <span className="mt-1 block text-xs text-content-tertiary">
                  {item.file_count} {t('knowledge.files', 'files')} · {item.chunk_count} {t('knowledge.chunks', 'chunks')} · {t('knowledge.updatedAt', 'Updated {{time}}', { time: formatTime(item.latest_updated_at) })}
                </span>
              </span>
              <span className="hidden text-xs text-content-tertiary lg:block">{t('knowledge.agenticFiles', 'Agentic files')}</span>
            </Link>
          ))}
        </div>
      ) : null}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('knowledge.create', 'New knowledge base')}</DialogTitle><DialogDescription>{t('knowledge.createHint', 'Name the collection by the questions it should answer.')}</DialogDescription></DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5"><Label htmlFor="kb-name">{t('name', 'Name')}</Label><Input id="kb-name" value={name} onChange={(event) => setName(event.target.value)} autoFocus /></div>
            <div className="space-y-1.5"><Label htmlFor="kb-description">{t('description', 'Description')}</Label><Textarea id="kb-description" value={description} onChange={(event) => setDescription(event.target.value)} /></div>
          </div>
          <DialogFooter><Button variant="outline" onClick={() => setCreateOpen(false)}>{t('cancel', 'Cancel')}</Button><Button disabled={!name.trim() || create.isPending} onClick={() => create.mutate()}>{create.isPending ? t('creating', 'Creating…') : t('create', 'Create')}</Button></DialogFooter>
        </DialogContent>
      </Dialog>
    </ManagementPageShell>
  );
}
