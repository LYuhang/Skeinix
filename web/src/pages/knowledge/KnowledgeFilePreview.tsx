import { useQuery } from '@tanstack/react-query';

import { FileWorkbenchPreview } from '@/components/files/FileWorkbenchPreview';
import { getKbFileRaw, type KbFile } from '@/lib/api/kb';

export function KnowledgeFilePreview({ kbId, file }: { kbId: string; file: KbFile }) {
  const query = useQuery({
    queryKey: ['knowledge-file-raw', kbId, file.id],
    queryFn: () => getKbFileRaw(kbId, file.id),
  });
  return (
    <FileWorkbenchPreview
      fileName={file.name}
      mimeType={file.mime_type}
      blob={query.data}
      loading={query.isPending}
      error={query.isError ? query.error.message : null}
    />
  );
}
