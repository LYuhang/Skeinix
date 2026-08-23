import { useRef, useState } from 'react';

import type { PreviewErrorInfo } from '@/lib/preview/protocol';
import { MediaPreviewSurface } from './MediaPreviewSurface';
import { PreviewErrorState } from './PreviewErrorState';
import type { PreviewRendererProps } from './renderer-types';

export function MediaPreviewRenderer({ descriptor, onReload }: PreviewRendererProps) {
  const url = descriptor.content?.url;
  const resourceKey = `${descriptor.revision}:${url ?? ''}`;
  const [failure, setFailure] = useState<{
    resourceKey: string;
    error: PreviewErrorInfo;
  } | null>(null);
  const autoReloadedRevision = useRef<string | null>(null);
  const error = failure?.resourceKey === resourceKey ? failure.error : null;
  if (!url) {
    return (
      <PreviewErrorState
        descriptor={descriptor}
        error={{ code: 'content_unavailable', params: {} }}
      />
    );
  }
  if (error) return <PreviewErrorState descriptor={descriptor} error={error} />;
  const kind = descriptor.renderer === 'image'
    ? 'image'
    : descriptor.renderer === 'audio'
      ? 'audio'
      : 'video';
  return (
    <MediaPreviewSurface
      key={resourceKey}
      url={url}
      name={descriptor.name}
      kind={kind}
      onError={() => {
        if (onReload && autoReloadedRevision.current !== descriptor.revision) {
          autoReloadedRevision.current = descriptor.revision;
          onReload();
          return;
        }
        setFailure({
          resourceKey,
          error: { code: 'render_failed', params: {} },
        });
      }}
    />
  );
}
