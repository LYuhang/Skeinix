export type FilePreviewKind =
  | 'image'
  | 'audio'
  | 'video'
  | 'markdown'
  | 'html'
  | 'python'
  | 'json'
  | 'delimited'
  | 'jsonl'
  | 'workbook'
  | 'pdf'
  | 'document'
  | 'presentation'
  | 'archive'
  | 'code'
  | 'text'
  | 'link'
  | 'binary'
  | 'unknown';

export interface FileCapability {
  kind: FilePreviewKind;
  mime: string;
  label: string;
  preview: boolean;
  source: boolean;
  editable: boolean;
  safeRenderedPreview: boolean;
  progressive: boolean;
}

const EXTENSION_MIME: Record<string, string> = {
  md: 'text/markdown',
  markdown: 'text/markdown',
  html: 'text/html',
  htm: 'text/html',
  py: 'text/x-python',
  pyw: 'text/x-python',
  json: 'application/json',
  jsonl: 'table/jsonl',
  ndjson: 'table/jsonl',
  csv: 'table/csv',
  tsv: 'table/tsv',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  xls: 'application/vnd.ms-excel',
  ods: 'application/vnd.oasis.opendocument.spreadsheet',
  pdf: 'application/pdf',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  odt: 'application/vnd.oasis.opendocument.text',
  rtf: 'application/rtf',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  odp: 'application/vnd.oasis.opendocument.presentation',
  zip: 'application/zip',
  gz: 'application/gzip',
  tar: 'application/x-tar',
  '7z': 'application/x-7z-compressed',
  rar: 'application/vnd.rar',
  png: 'image/png',
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  gif: 'image/gif',
  webp: 'image/webp',
  bmp: 'image/bmp',
  tif: 'image/tiff',
  tiff: 'image/tiff',
  svg: 'image/svg+xml',
  mp3: 'audio/mpeg',
  wav: 'audio/wav',
  m4a: 'audio/mp4',
  aac: 'audio/aac',
  flac: 'audio/flac',
  oga: 'audio/ogg',
  mp4: 'video/mp4',
  webm: 'video/webm',
  ogg: 'video/ogg',
  ogv: 'video/ogg',
  mov: 'video/quicktime',
  m4v: 'video/x-m4v',
  txt: 'text/plain',
  log: 'text/plain',
  js: 'text/javascript',
  jsx: 'text/jsx',
  ts: 'text/typescript',
  tsx: 'text/tsx',
  css: 'text/css',
  xml: 'application/xml',
  yml: 'text/yaml',
  yaml: 'text/yaml',
  toml: 'text/toml',
  sh: 'text/x-shellscript',
};

function extension(path: string): string {
  const leaf = path.split('/').at(-1) ?? '';
  const dot = leaf.lastIndexOf('.');
  return dot >= 0 ? leaf.slice(dot + 1).toLocaleLowerCase() : '';
}

function normalizedMime(contentType?: string | null): string {
  return (contentType ?? '').split(';', 1)[0]!.trim().toLocaleLowerCase();
}

function capability(kind: FilePreviewKind, mime: string, label: string): FileCapability {
  const source = ['markdown', 'html', 'python', 'json', 'delimited', 'jsonl', 'code', 'text'].includes(kind);
  const preview = !['binary', 'unknown', 'document', 'presentation', 'archive'].includes(kind);
  return {
    kind,
    mime,
    label,
    preview,
    source,
    editable: source,
    safeRenderedPreview: kind === 'markdown' || kind === 'html',
    progressive: ['video', 'delimited', 'jsonl', 'workbook', 'text', 'python', 'json', 'code'].includes(kind),
  };
}

export function resolveFileCapability(path: string, contentType?: string | null): FileCapability {
  const ext = extension(path);
  let mime = normalizedMime(contentType);
  const inferredMime = EXTENSION_MIME[ext];

  // Specific file extensions correct generic or missing server content types;
  // a specific authoritative MIME still wins over the filename.
  if (
    !mime
    || mime === 'application/octet-stream'
    || mime === 'binary/octet-stream'
    || (mime === 'text/plain' && inferredMime && inferredMime !== 'text/plain')
  ) {
    mime = inferredMime ?? mime;
  }

  if (mime.startsWith('image/')) return capability('image', mime, 'Image');
  if (mime.startsWith('audio/')) return capability('audio', mime, 'Audio');
  if (mime.startsWith('video/')) return capability('video', mime, 'Video');
  if (mime === 'text/markdown' || mime === 'text/x-markdown') return capability('markdown', mime, 'Markdown');
  if (mime === 'text/html' || mime === 'application/xhtml+xml') return capability('html', mime, 'HTML');
  if (mime.includes('python')) return capability('python', mime, 'Python');
  if (mime === 'application/json' || mime === 'json') return capability('json', mime, 'JSON');
  if (mime === 'table/jsonl' || mime === 'application/x-ndjson') return capability('jsonl', mime, 'JSONL');
  if (mime === 'table/csv' || mime === 'text/csv') return capability('delimited', 'table/csv', 'CSV');
  if (mime === 'table/tsv' || mime === 'text/tab-separated-values') return capability('delimited', 'table/tsv', 'TSV');
  if (
    mime === 'table/xlsx'
    || mime.includes('spreadsheetml.sheet')
    || mime === 'application/vnd.ms-excel'
    || mime === 'application/vnd.oasis.opendocument.spreadsheet'
  ) return capability('workbook', mime, 'Excel');
  if (mime === 'application/pdf') return capability('pdf', mime, 'PDF');
  if (
    mime === 'application/msword'
    || mime.includes('wordprocessingml.document')
    || mime === 'application/vnd.oasis.opendocument.text'
    || mime === 'application/rtf'
  ) return capability('document', mime, 'Document');
  if (
    mime === 'application/vnd.ms-powerpoint'
    || mime.includes('presentationml.presentation')
    || mime === 'application/vnd.oasis.opendocument.presentation'
  ) return capability('presentation', mime, 'Presentation');
  if (
    mime === 'application/zip'
    || mime === 'application/gzip'
    || mime === 'application/x-tar'
    || mime === 'application/x-7z-compressed'
    || mime === 'application/vnd.rar'
  ) return capability('archive', mime, 'Archive');
  if (mime.startsWith('link/')) return capability('link', mime, 'Link');
  const structuredTextMime = /javascript|yaml|toml/.test(mime)
    || mime === 'application/xml'
    || mime === 'text/xml'
    || mime.endsWith('+xml');
  if (mime.startsWith('text/') || structuredTextMime) {
    const codeExtensions = new Set([
      'c', 'cc', 'cpp', 'css', 'go', 'h', 'hpp', 'java', 'js', 'jsx', 'rs',
      'scss', 'sh', 'sql', 'toml', 'ts', 'tsx', 'xml', 'yaml', 'yml',
    ]);
    return capability(
      codeExtensions.has(ext) ? 'code' : 'text',
      mime,
      ext ? ext.toLocaleUpperCase() : 'Text',
    );
  }
  if (mime === 'application/octet-stream' || mime === 'binary/octet-stream') {
    return capability('binary', mime, ext ? ext.toLocaleUpperCase() : 'Binary');
  }
  return capability('unknown', mime || 'application/octet-stream', ext ? ext.toLocaleUpperCase() : 'File');
}

export function isTextFileCapability(value: FileCapability): boolean {
  return ['markdown', 'html', 'python', 'json', 'delimited', 'jsonl', 'code', 'text'].includes(value.kind);
}
