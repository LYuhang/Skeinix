import type { PreviewDescriptorV1 } from '@/lib/preview/protocol';
import { LanguageDescription } from '@codemirror/language';
import { languages } from '@codemirror/language-data';

export interface CodePreviewLanguage {
  id: string;
  description: LanguageDescription | null;
}

const CODE_EXTENSIONS = new Set([
  'css',
  'c',
  'cc',
  'cpp',
  'cs',
  'go',
  'graphql',
  'h',
  'hpp',
  'ini',
  'java',
  'js',
  'jsx',
  'json',
  'kt',
  'kts',
  'lua',
  'php',
  'rb',
  'rs',
  'scala',
  'sh',
  'sql',
  'swift',
  'svelte',
  'toml',
  'ts',
  'tsx',
  'vue',
  'xml',
  'yaml',
  'yml',
]);

const CODE_FILENAMES = new Set([
  'dockerfile',
  'makefile',
  'jenkinsfile',
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
  const sourceBasename = descriptor.name.split('/').at(-1) ?? '';
  const basename = sourceBasename.toLocaleLowerCase();
  const extension = fileExtension(sourceBasename);
  const contentType = descriptor.contentType.split(';', 1)[0]?.trim().toLocaleLowerCase() ?? '';

  const isCode = extension === 'py'
    || extension === 'pyw'
    || CODE_EXTENSIONS.has(extension)
    || CODE_FILENAMES.has(basename)
    || /python|javascript|typescript|json|xml|yaml|toml|shellscript/.test(contentType);
  if (!isCode) return null;
  const byFilename = LanguageDescription.matchFilename(languages, sourceBasename);
  const mimeName = [
    'python', 'javascript', 'typescript', 'json', 'xml', 'yaml', 'toml', 'shell',
  ].find((name) => contentType.includes(name));
  const byMime = mimeName
    ? LanguageDescription.matchLanguageName(languages, mimeName, true)
    : null;
  return {
    id: byFilename?.name ?? byMime?.name ?? (extension || basename),
    description: byFilename ?? byMime,
  };
}
