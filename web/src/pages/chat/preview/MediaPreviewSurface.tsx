import { useState } from 'react';
import { Minus, Plus, RotateCcw } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { Button } from '@/components/ui/button';

const MIN_ZOOM = 0.5;
const MAX_ZOOM = 4;
const ZOOM_STEP = 0.25;

export interface MediaPreviewSurfaceProps {
  url: string;
  name: string;
  kind: 'image' | 'audio' | 'video';
  onError: () => void;
}

/** Shared media content surface for the unified Preview renderer. */
export function MediaPreviewSurface({
  url,
  name,
  kind,
  onError,
}: MediaPreviewSurfaceProps) {
  const { t } = useTranslation();
  const [zoom, setZoom] = useState(1);

  if (kind === 'image') {
    return (
      <div className="relative flex h-full min-h-0 flex-col bg-surface-sunken">
        <div
          className="absolute right-3 top-3 z-10 flex items-center gap-1 rounded-md border border-edge-subtle bg-surface-raised p-1"
          role="toolbar"
          aria-label={t('preview.media.zoomControls', 'Image zoom controls')}
        >
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            disabled={zoom <= MIN_ZOOM}
            onClick={() => setZoom((current) => Math.max(MIN_ZOOM, current - ZOOM_STEP))}
            aria-label={t('preview.media.zoomOut', 'Zoom out')}
          >
            <Minus />
          </Button>
          <span
            className="min-w-12 text-center text-xs tabular-nums text-muted-foreground"
            aria-live="polite"
          >
            {Math.round(zoom * 100)}%
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            disabled={zoom >= MAX_ZOOM}
            onClick={() => setZoom((current) => Math.min(MAX_ZOOM, current + ZOOM_STEP))}
            aria-label={t('preview.media.zoomIn', 'Zoom in')}
          >
            <Plus />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            disabled={zoom === 1}
            onClick={() => setZoom(1)}
            aria-label={t('preview.media.resetZoom', 'Reset zoom')}
          >
            <RotateCcw />
          </Button>
        </div>
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-6 pt-16">
          <img
            src={url}
            alt={name}
            className="max-h-full max-w-full object-contain transition-transform duration-feedback motion-reduce:transition-none"
            style={{ transform: `scale(${zoom})` }}
            onDoubleClick={() => setZoom(1)}
            onError={onError}
          />
        </div>
      </div>
    );
  }

  if (kind === 'audio') {
    return (
      <div className="flex h-full items-center justify-center p-6">
        <audio
          controls
          preload="metadata"
          src={url}
          className="w-full max-w-2xl"
          onError={onError}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full items-center justify-center bg-black p-4">
      <video
        controls
        preload="metadata"
        src={url}
        className="max-h-full max-w-full"
        onError={onError}
      >
        {t('preview.media.videoUnsupported', 'Your browser cannot play this video.')}
      </video>
    </div>
  );
}
