import type { PreviewDescriptorV1 } from '@/lib/preview/protocol';

export type CodePreviewLanguage = 'python' | 'plain';

const CODE_EXTENSIONS = new Set([
  'css',
  'ini',
  'js',
  'jsx',
  'json',
  'sh',
  'toml',
  'ts',
  'tsx',
  'xml',
  'yaml',
  'yml',
]);

const CODE_FILENAMES = new Set([
  'dockerfile',
  'makefile',
]);

function fileExtension(name: string): string {
  const basename = name.split('/').at(-1)?.toLocaleLowerCase() ?? '';
  const dot = basename.lastIndexOf('.');
  return dot >= 0 ? basename.slice(dot + 1) : '';
}

/**
 * Decide whether the unified text renderer should use its code surface.
 *
 * The preview API intentionally exposes one `text` renderer for source files
 * and prose. Filename and MIME metadata let the client add code affordances
 * without creating a second preview protocol or persistence path.
 */
export function resolveCodePreviewLanguage(
  descriptor: Pick<PreviewDescriptorV1, 'contentType' | 'name'>,
): CodePreviewLanguage | null {
  const basename = descriptor.name.split('/').at(-1)?.toLocaleLowerCase() ?? '';
  const extension = fileExtension(basename);
  const contentType = descriptor.contentType.split(';', 1)[0]?.trim().toLocaleLowerCase() ?? '';

  if (extension === 'py' || extension === 'pyw' || contentType.includes('python')) {
    return 'python';
  }
  if (
    CODE_EXTENSIONS.has(extension)
    || CODE_FILENAMES.has(basename)
    || /javascript|typescript|json|xml|yaml|toml|shellscript/.test(contentType)
  ) {
    return 'plain';
  }
  return null;
}
