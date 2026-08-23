import { useEffect, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Download, FileQuestion, LoaderCircle } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';
import { getKbFileRaw, type KbFile } from '@/lib/api/kb';

function isText(file: KbFile) {
  return file.mime_type.startsWith('text/')
    || ['json', 'markdown', 'txt', 'csv', 'html'].includes(file.parser_type);
}

export function KnowledgeFilePreview({ kbId, file }: { kbId: string; file: KbFile }) {
  const { t } = useTranslation();
  const query = useQuery({
    queryKey: ['knowledge-file-raw', kbId, file.id],
    queryFn: () => getKbFileRaw(kbId, file.id),
  });
  const objectUrl = useMemo(
    () => query.data ? URL.createObjectURL(query.data) : null,
    [query.data],
  );
  useEffect(() => () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
  }, [objectUrl]);
  const text = useQuery({
    queryKey: ['knowledge-file-raw-text', kbId, file.id],
    queryFn: () => query.data!.text(),
    enabled: Boolean(query.data && isText(file)),
  });

  if (query.isPending) {
    return (
      <div className="flex min-h-64 items-center justify-center gap-2 text-sm text-content-secondary">
        <LoaderCircle className="size-4 animate-spin" />
        {t('knowledge.preview.loading', 'Loading preview…')}
      </div>
    );
  }
  if (query.isError || !objectUrl) {
    return (
      <div className="flex min-h-64 items-center justify-center text-sm text-state-danger">
        {t('knowledge.preview.failed', 'This file could not be previewed.')}
      </div>
    );
  }

  const download = (
    <Button variant="outline" size="sm" asChild>
      <a href={objectUrl} download={file.name.split('/').pop()}>
        <Download className="size-4" />
        {t('download', 'Download')}
      </a>
    </Button>
  );
  if (file.mime_type.startsWith('image/')) {
    return <div className="flex min-h-64 items-center justify-center p-4"><img src={objectUrl} alt={file.name} className="max-h-[34rem] max-w-full object-contain" /></div>;
  }
  if (file.mime_type.startsWith('video/')) {
    return <div className="p-4"><video src={objectUrl} controls className="mx-auto max-h-[34rem] max-w-full" /></div>;
  }
  if (file.mime_type.startsWith('audio/')) {
    return <div className="flex min-h-52 items-center justify-center p-4"><audio src={objectUrl} controls className="w-full max-w-xl" /></div>;
  }
  if (file.mime_type === 'application/pdf') {
    return <iframe title={file.name} src={objectUrl} className="h-[34rem] w-full border-0" />;
  }
  if (isText(file)) {
    if (text.isPending) return null;
    const value = text.data ?? '';
    if (file.parser_type === 'markdown' || /(?:^|\/)readme\.md$/i.test(file.name)) {
      return (
        <article className="prose prose-sm max-w-none overflow-auto p-6 dark:prose-invert">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
        </article>
      );
    }
    return <pre className="max-h-[34rem] overflow-auto whitespace-pre-wrap break-words p-5 font-mono text-xs leading-5">{value}</pre>;
  }
  return (
    <div className="flex min-h-64 flex-col items-center justify-center gap-3 p-6 text-center">
      <FileQuestion className="size-9 text-content-tertiary" />
      <div>
        <p className="text-sm font-medium">{t('knowledge.preview.unsupported', 'Preview is not available for this file type')}</p>
        <p className="mt-1 text-xs text-content-secondary">{file.mime_type}</p>
      </div>
      {download}
    </div>
  );
}
