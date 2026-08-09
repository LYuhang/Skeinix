import type { PreviewDescriptorV1 } from '@/lib/preview/protocol';

export interface PreviewRendererProps {
  descriptor: PreviewDescriptorV1;
  loadAllowed: boolean;
  onDirtyChange: (dirty: boolean) => void;
  onOpenFile?: (path: string) => void;
}
