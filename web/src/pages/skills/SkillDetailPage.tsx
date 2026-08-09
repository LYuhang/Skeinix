import { useCallback, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams, useSearchParams } from 'react-router';
import { toast } from 'sonner';
import { BookOpenText, ExternalLink, GitCommitHorizontal, Loader2, Pencil, Save, Share2, ShieldCheck, Trash2 } from 'lucide-react';

import { Markdown } from '@/components/agent-sidebar/Markdown';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useDeleteSkill, usePublishSkillVersion, useSaveSkillDraft, useSkill, useSkillDraft, useSkillVersion, useSkillVersions } from '@/lib/api/queries/skills';
import { getSkillFile, getSkillVersionFile } from '@/lib/api/skills';
import { SkillFileBrowser } from '@/pages/skills/SkillFileBrowser';
import { StatusBadge } from '@/components/ui/status';
import { useFormatDateTime } from '@/lib/timezone';
import { EntityDetailShell } from '@/components/layout/entity-detail-shell';
import { ResourceShareDialog } from '@/components/modals/ResourceShareDialog';
import { ActionableError } from '@/components/presentation/ActionableError';

export function SkillDetailPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const formatTime = useFormatDateTime();
  const [params, setParams] = useSearchParams();
  const { id } = useParams();
  const query = useSkill(id);
  const selectedRevisionId = params.get('revision') ?? undefined;
  const versionsQuery = useSkillVersions(id);
  const selectedVersionQuery = useSkillVersion(id, selectedRevisionId);
  const isCustom = query.data?.source === 'custom';
  const canUpdate = query.data?.access?.capabilities.includes('update') ?? false;
  const draftQuery = useSkillDraft(id, isCustom && canUpdate);
  const deleteMutation = useDeleteSkill();
  const saveDraftMutation = useSaveSkillDraft();
  const publishMutation = usePublishSkillVersion();
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [draftDirty, setDraftDirty] = useState(false);
  const [versionDialog, setVersionDialog] = useState(false);
  const [nextVersion, setNextVersion] = useState('');
  const [hydratedDraftIdentity, setHydratedDraftIdentity] = useState('');
  const loadFile = useCallback(
    (path: string) => selectedRevisionId
      ? getSkillVersionFile(id as string, selectedRevisionId, path)
      : getSkillFile(id as string, path),
    [id, selectedRevisionId],
  );
  const selectFileInUrl = useCallback((path: string) => {
    const next = new URLSearchParams(params);
    next.set('tab', 'files');
    next.set('file', path);
    setParams(next, { replace: true });
  }, [params, setParams]);
  const draftIdentity = `${draftQuery.data?.base_revision_hash ?? ''}:${draftQuery.data?.draft_hash ?? ''}`;
  if (draftQuery.data && hydratedDraftIdentity !== draftIdentity) {
    setHydratedDraftIdentity(draftIdentity);
    setDraftText(draftQuery.data.skill_md);
    setDraftDirty(false);
    if (params.get('edit') === '1') setEditing(true);
  }

  if (query.isLoading) return <div className="page-shell page-shell-contained"><div className="page-content max-w-6xl"><div className="empty-state">{t('skills.loading', 'Loading…')}</div></div></div>;
  if (query.isError || !query.data) return <div className="page-shell page-shell-contained"><div className="page-content max-w-6xl"><ActionableError title={t('skills.not_found', 'This Skill No Longer Exists.')} description={t('skills.load_error_hint', 'Return to the skill list or try loading this skill again.')} actionLabel={t('retry', 'Retry')} onAction={() => void query.refetch()} technicalDetails={query.error instanceof Error ? query.error.message : undefined} technicalDetailsLabel={t('common.technicalDetails', 'Technical details')} /></div></div>;
  if (selectedRevisionId && selectedVersionQuery.isLoading) return <div className="page-shell page-shell-contained"><div className="page-content max-w-6xl"><div className="empty-state">{t('skills.loading', 'Loading…')}</div></div></div>;
  if (selectedRevisionId && (selectedVersionQuery.isError || !selectedVersionQuery.data)) return <div className="page-shell page-shell-contained"><div className="page-content max-w-6xl"><ActionableError title={t('skills.version_not_found', 'This Skill version is no longer available.')} description={t('skills.version_load_error_hint', 'Choose another version or try loading this version again.')} actionLabel={t('retry', 'Retry')} onAction={() => void selectedVersionQuery.refetch()} technicalDetails={selectedVersionQuery.error instanceof Error ? selectedVersionQuery.error.message : undefined} technicalDetailsLabel={t('common.technicalDetails', 'Technical details')} /></div></div>;
  const skill = query.data;
  const capabilities = new Set(skill.access?.capabilities ?? []);
  const selectedVersion = selectedVersionQuery.data;
  const viewed = selectedVersion ?? skill;
  const viewingHistory = Boolean(selectedRevisionId);

  const leaveEditor = () => {
    setEditing(false);
    setDraftText(draftQuery.data?.skill_md ?? skill.skill_md);
    setDraftDirty(false);
    const next = new URLSearchParams(params);
    next.delete('edit');
    setParams(next, { replace: true });
  };
  const startEditing = () => {
    const next = new URLSearchParams(params);
    next.set('tab', 'instructions');
    next.set('edit', '1');
    next.delete('revision');
    setEditing(true);
    setParams(next, { replace: true });
  };
  const saveDraft = async () => {
    if (!id) return;
    try {
      await saveDraftMutation.mutateAsync({ id, skillMd: draftText });
      setDraftDirty(false);
      toast.success(t('skills.custom.draft_saved', 'Draft saved'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };
  const openVersionDialog = () => {
    setNextVersion(String(skill.version + 1));
    setVersionDialog(true);
  };
  const publishVersion = async () => {
    if (!id) return;
    const version = Number(nextVersion);
    if (!Number.isInteger(version) || version <= skill.version) {
      toast.error(t('skills.custom.version_invalid', 'Version must be a whole number greater than the published version.'));
      return;
    }
    try {
      await publishMutation.mutateAsync({ id, version });
      setVersionDialog(false);
      leaveEditor();
      toast.success(t('skills.custom.version_created', 'New Skill version created'));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  const uninstall = async () => {
    try {
      await deleteMutation.mutateAsync(skill.id);
      toast.success(t('skills.deleted', 'Skill Uninstalled'));
      navigate('/skills');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  const sourceName = skill.source === 'openai'
    ? t('skills.source.openai', 'OpenAI Curated')
    : skill.source === 'anthropic'
      ? t('skills.source.anthropic', 'Anthropic Public')
      : skill.source === 'custom'
        ? t('skills.source.custom', 'Custom')
        : t('skills.source.unknown', 'Imported');
  const requestedTab = params.get('tab');
  const activeTab = ['overview', 'instructions', 'files', 'requirements'].includes(requestedTab ?? '') ? requestedTab! : 'overview';
  const selectedFile = params.get('file') ?? undefined;
  const selectVersion = (value: string) => {
    const next = new URLSearchParams(params);
    if (value === 'latest') next.delete('revision');
    else next.set('revision', value);
    next.delete('edit');
    next.delete('file');
    setEditing(false);
    setParams(next, { replace: true });
  };
  return (
    <>
      <EntityDetailShell
        resourceKind="skill"
        backTo="/skills"
        backLabel={t('skills.back', 'Back')}
        title={viewed.name}
        description={viewed.description}
        icon={BookOpenText}
        status={<div className="flex items-center gap-2">
          <StatusBadge status="success">{t('skills.installed_button', 'Installed')}</StatusBadge>
          {skill.has_draft ? <StatusBadge status="warning">{t('skills.custom.unpublished', 'Unpublished changes')}</StatusBadge> : null}
          {viewingHistory ? <StatusBadge status="neutral">{t('skills.custom.historical', 'Historical version')}</StatusBadge> : null}
        </div>}
        metadata={<><span>{sourceName}</span><span>v{viewed.version}</span><span>{t('skills.files_count', { count: viewed.files.length, defaultValue: '{{count}} Files' })}</span></>}
        actions={<>
            <Select value={selectedRevisionId ?? 'latest'} onValueChange={selectVersion}>
              <SelectTrigger className="h-9 w-44" aria-label={t('skills.custom.select_version', 'Select version')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="latest">
                  {t('skills.custom.latest_version', { version: skill.version, defaultValue: 'Latest · v{{version}}' })}
                </SelectItem>
                {(versionsQuery.data ?? []).filter((revision) => !revision.is_latest).map((revision) => (
                  <SelectItem key={revision.revision_id} value={revision.revision_id}>
                    v{revision.version}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {skill.source_url ? <Button variant="outline" size="sm" asChild><a href={skill.source_url} target="_blank" rel="noreferrer"><ExternalLink />{t('skills.catalog.source', 'Source')}</a></Button> : null}
            {isCustom && capabilities.has('update') && !editing && !viewingHistory ? (
              <Button
                variant="outline"
                size="sm"
                disabled={draftQuery.isLoading || !draftQuery.data}
                onClick={startEditing}
              >
                <Pencil />{t('skills.edit', 'Edit')}
              </Button>
            ) : null}
            {isCustom && capabilities.has('update') && editing && !viewingHistory ? (
              <>
                <Button variant="ghost" size="sm" onClick={leaveEditor} disabled={saveDraftMutation.isPending || publishMutation.isPending}>
                  {t('skills.cancel', 'Cancel')}
                </Button>
                <Button variant="outline" size="sm" onClick={() => void saveDraft()} disabled={!draftDirty || saveDraftMutation.isPending || publishMutation.isPending}>
                  {saveDraftMutation.isPending ? <Loader2 className="animate-spin" /> : <Save />}
                  {t('skills.custom.save_draft', 'Save draft')}
                </Button>
                <Button size="sm" onClick={openVersionDialog} disabled={draftDirty || !draftQuery.data?.has_changes || saveDraftMutation.isPending || publishMutation.isPending}>
                  <GitCommitHorizontal />{t('skills.custom.new_version', 'New version')}
                </Button>
              </>
            ) : null}
            {capabilities.has('manage_access') ? (
              <Button variant="outline" size="sm" onClick={() => setShareOpen(true)}><Share2 />{t('skills.share', 'Share Skill')}</Button>
            ) : null}
            {capabilities.has('delete') ? (
              <Button variant="destructive" size="sm" onClick={() => setConfirmDelete(true)}><Trash2 />{t('skills.delete', 'Uninstall')}</Button>
            ) : null}
          </>}
      >

        <Tabs value={activeTab} onValueChange={(tab) => { const next = new URLSearchParams(params); next.set('tab', tab); setParams(next, { replace: true }); }} className="flex min-h-0 flex-1 flex-col gap-4">
          <TabsList variant="underline" className="h-auto w-full shrink-0 justify-start">
            <TabsTrigger value="overview">{t('skills.detail.tab.overview', 'Overview')}</TabsTrigger>
            <TabsTrigger value="instructions">{t('skills.detail.tab.instructions', 'Instructions')}</TabsTrigger>
            <TabsTrigger value="files">{t('skills.detail.tab.files', 'Files')}</TabsTrigger>
            <TabsTrigger value="requirements">{t('skills.detail.tab.requirements', 'Requirements')}</TabsTrigger>
          </TabsList>
          <TabsContent value="overview" className="page-scroll-region mt-0 min-h-0 flex-1 max-w-3xl pr-2">
            <h2 className="text-sm font-semibold">{t('skills.detail.description', 'Description')}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{viewed.description}</p>
            <dl className="mt-6 grid grid-cols-1 gap-x-8 gap-y-4 border-t pt-5 sm:grid-cols-2"><Info label={t('skills.detail.source', 'Source')} value={sourceName} /><Info label={t('skills.detail.version', 'Version')} value={`v${viewed.version}`} /><Info label={t('skills.detail.created', 'Installed')} value={formatTime(viewingHistory ? selectedVersion?.created_at ?? null : skill.created_at)} /><Info label={t('skills.detail.updated', 'Updated')} value={formatTime(skill.updated_at)} /></dl>
          </TabsContent>
          <TabsContent value="instructions" className="page-scroll-region mt-0 min-h-0 flex-1 max-w-4xl p-1">
            {isCustom && capabilities.has('update') && editing && !viewingHistory ? (
              <div className="space-y-3">
                <div className="rounded-md border border-edge-subtle bg-surface-work px-3 py-2 text-xs text-muted-foreground">
                  {t('skills.custom.working_tree_help', 'You are editing a durable draft. Saving does not change the version available to agents; create a new version when it is ready.')}
                </div>
                <Textarea
                  aria-label="SKILL.md"
                  value={draftText}
                  onChange={(event) => {
                    setDraftText(event.target.value);
                    setDraftDirty(event.target.value !== draftQuery.data?.skill_md);
                  }}
                  spellCheck={false}
                  className="min-h-[32rem] resize-y rounded-md bg-surface-work font-mono text-xs leading-5"
                />
              </div>
            ) : (
              <Markdown className="text-sm leading-6">{viewed.body}</Markdown>
            )}
          </TabsContent>
          <TabsContent value="files" className="mt-0 min-h-0 flex-1 overflow-hidden"><SkillFileBrowser persistKey={`${skill.id}:${selectedRevisionId ?? 'latest'}`} files={viewed.files} skillMd={viewed.skill_md} loadFile={loadFile} selectedPath={selectedFile} onSelectedPathChange={selectFileInUrl} labels={{ files: t('skills.detail.files.bundle', 'Package Files'), loading: t('skills.detail.files.loading', 'Loading File…'), failed: t('skills.detail.files.failed', 'Could Not Load File'), binary: t('skills.detail.files.binary', 'Binary File Preview Is Not Available.') }} /></TabsContent>
          <TabsContent value="requirements" className="page-scroll-region mt-0 min-h-0 flex-1 max-w-3xl space-y-5 pr-2">
            <section><h2 className="text-sm font-semibold">{t('skills.tools_title', 'Allowed Tools')}</h2>{viewed.allowed_tools.length ? <div className="mt-3 flex flex-wrap gap-2">{viewed.allowed_tools.map((tool) => <span key={tool} className="rounded bg-secondary px-2 py-1 font-mono text-xs text-secondary-foreground">{tool}</span>)}</div> : <p className="mt-2 text-sm text-muted-foreground">{t('skills.no_tools', 'No Tool Requirements Declared.')}</p>}</section>
            <div className="flex items-start gap-3 border-t pt-5"><ShieldCheck className="mt-0.5 h-5 w-5 text-state-success" /><div><div className="text-sm font-medium">{t('skills.detail.validation.title', 'SKILL.md Validated')}</div><p className="mt-1 text-sm text-muted-foreground">{t('skills.detail.validation.available', 'This package is available to agents through the installed Skill catalog.')}</p></div></div>
          </TabsContent>
        </Tabs>
      </EntityDetailShell>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}><DialogContent className="sm:max-w-md"><DialogHeader><DialogTitle>{t('skills.delete_title', 'Uninstall This Skill?')}</DialogTitle><DialogDescription>{t('skills.delete_confirm', 'The agent will no longer be able to load this Skill. Its installed bundle will be removed.')}</DialogDescription></DialogHeader><DialogFooter><Button variant="outline" onClick={() => setConfirmDelete(false)} disabled={deleteMutation.isPending}>{t('skills.cancel', 'Cancel')}</Button><Button variant="destructive" onClick={() => void uninstall()} disabled={deleteMutation.isPending}>{t('skills.delete', 'Uninstall')}</Button></DialogFooter></DialogContent></Dialog>
      <ResourceShareDialog
        open={shareOpen}
        onOpenChange={setShareOpen}
        resourceKind="skill"
        resourceId={skill.id}
        resourceName={skill.name}
        effectiveRole={skill.access?.effective_role}
        accessSource={skill.access?.source}
      />
      <Dialog open={versionDialog} onOpenChange={setVersionDialog}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('skills.custom.version_title', 'Create a new Skill version')}</DialogTitle>
            <DialogDescription>
              {t('skills.custom.version_help', 'This creates an immutable revision from the saved draft and makes it the version used by new agent turns.')}
            </DialogDescription>
          </DialogHeader>
          <div>
            <label htmlFor="skill-version" className="mb-1.5 block text-sm font-medium">{t('skills.detail.version', 'Version')}</label>
            <Input
              id="skill-version"
              type="number"
              min={skill.version + 1}
              step={1}
              value={nextVersion}
              onChange={(event) => setNextVersion(event.target.value)}
            />
            <p className="mt-2 text-xs text-muted-foreground">
              {t('skills.custom.current_version', { version: skill.version, defaultValue: 'Current published version: v{{version}}' })}
            </p>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setVersionDialog(false)} disabled={publishMutation.isPending}>{t('skills.cancel', 'Cancel')}</Button>
            <Button onClick={() => void publishVersion()} disabled={publishMutation.isPending}>
              {publishMutation.isPending ? <Loader2 className="animate-spin" /> : <GitCommitHorizontal />}
              {t('skills.custom.publish', 'Create version')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function Info({ label, value }: { label: string; value: string }) { return <div><dt className="text-xs text-muted-foreground">{label}</dt><dd className="mt-1 text-sm">{value}</dd></div>; }
