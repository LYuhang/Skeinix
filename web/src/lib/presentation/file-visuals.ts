import {
  FileArchive,
  FileAudio2,
  FileCode2,
  FileImage,
  FileQuestion,
  FileSpreadsheet,
  FileText,
  FileVideo2,
  Folder,
  FolderOpen,
  Presentation,
  type LucideIcon,
} from 'lucide-react';

export type FileVisualKind =
  | 'folder'
  | 'document'
  | 'pdf'
  | 'spreadsheet'
  | 'presentation'
  | 'code'
  | 'image'
  | 'video'
  | 'audio'
  | 'archive'
  | 'unknown';

export interface FileVisual {
  kind: FileVisualKind;
  icon: LucideIcon;
  foregroundClass: string;
  surfaceClass: string;
}

const visuals: Readonly<Record<FileVisualKind, Omit<FileVisual, 'kind'>>> = {
  folder: { icon: Folder, foregroundClass: 'text-resource-storage', surfaceClass: 'bg-resource-storage/10' },
  document: { icon: FileText, foregroundClass: 'text-resource-chat', surfaceClass: 'bg-resource-chat/10' },
  pdf: { icon: FileText, foregroundClass: 'text-state-danger', surfaceClass: 'bg-state-danger/10' },
  spreadsheet: { icon: FileSpreadsheet, foregroundClass: 'text-resource-skill', surfaceClass: 'bg-resource-skill/10' },
  presentation: { icon: Presentation, foregroundClass: 'text-resource-deployment', surfaceClass: 'bg-resource-deployment/10' },
  code: { icon: FileCode2, foregroundClass: 'text-resource-workflow', surfaceClass: 'bg-resource-workflow/10' },
  image: { icon: FileImage, foregroundClass: 'text-resource-mcp', surfaceClass: 'bg-resource-mcp/10' },
  video: { icon: FileVideo2, foregroundClass: 'text-resource-mcp', surfaceClass: 'bg-resource-mcp/10' },
  audio: { icon: FileAudio2, foregroundClass: 'text-resource-mcp', surfaceClass: 'bg-resource-mcp/10' },
  archive: { icon: FileArchive, foregroundClass: 'text-content-tertiary', surfaceClass: 'bg-surface-sunken' },
  unknown: { icon: FileQuestion, foregroundClass: 'text-content-tertiary', surfaceClass: 'bg-surface-sunken' },
};

const extensionKinds: Readonly<Record<string, FileVisualKind>> = {
  pdf: 'pdf',
  doc: 'document', docx: 'document', odt: 'document', rtf: 'document', txt: 'document', md: 'document',
  csv: 'spreadsheet', tsv: 'spreadsheet', xls: 'spreadsheet', xlsx: 'spreadsheet', ods: 'spreadsheet',
  ppt: 'presentation', pptx: 'presentation', odp: 'presentation',
  js: 'code', jsx: 'code', ts: 'code', tsx: 'code', py: 'code', java: 'code', go: 'code', rs: 'code',
  c: 'code', cc: 'code', cpp: 'code', h: 'code', hpp: 'code', css: 'code', scss: 'code', html: 'code',
  json: 'code', jsonl: 'code', yaml: 'code', yml: 'code', toml: 'code', xml: 'code', sh: 'code', sql: 'code',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', webp: 'image', svg: 'image', avif: 'image',
  mp4: 'video', webm: 'video', mov: 'video', avi: 'video', mkv: 'video',
  mp3: 'audio', wav: 'audio', m4a: 'audio', ogg: 'audio', flac: 'audio',
  zip: 'archive', tar: 'archive', gz: 'archive', bz2: 'archive', '7z': 'archive', rar: 'archive',
};

export function fileVisualFor({
  fileName,
  mimeType,
  directory = false,
  open = false,
}: {
  fileName?: string | null;
  mimeType?: string | null;
  directory?: boolean;
  open?: boolean;
}): FileVisual {
  if (directory) {
    return { kind: 'folder', ...visuals.folder, icon: open ? FolderOpen : Folder };
  }
  const extension = fileName?.split('.').pop()?.toLocaleLowerCase() ?? '';
  let kind = extensionKinds[extension];
  if (!kind && mimeType) {
    if (mimeType === 'application/pdf') kind = 'pdf';
    else if (mimeType.startsWith('image/')) kind = 'image';
    else if (mimeType.startsWith('video/')) kind = 'video';
    else if (mimeType.startsWith('audio/')) kind = 'audio';
    else if (mimeType.startsWith('text/')) kind = 'document';
  }
  const resolved = kind ?? 'unknown';
  return { kind: resolved, ...visuals[resolved] };
}
