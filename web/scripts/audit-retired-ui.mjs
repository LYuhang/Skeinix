import { access, readdir, readFile } from 'node:fs/promises';
import { constants } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const root = resolve(import.meta.dirname, '..');
const srcRoot = join(root, 'src');

const retiredPaths = [
  'src/app/AppHeader.tsx',
  'src/components/modals/AddNodeDialog.tsx',
  'src/components/modals/PublishTemplateDialog.tsx',
  'src/components/modals/ShareWorkflowDialog.tsx',
  'src/components/modals/TemplateMarketDialog.tsx',
  'src/components/ui/anchored-context-menu.tsx',
  'src/pages/canvas/explorer/TemplateCard.tsx',
  'src/pages/canvas/templateNode.ts',
  'src/pages/chat/ChatFileMediaPreview.tsx',
  'src/pages/chat/ChatFileViewer.tsx',
  'src/pages/chat/ChatPythonCodeEditor.tsx',
  'src/pages/chat/grid-edit-history.ts',
  'src/pages/chat/use-virtual-grid-rows.ts',
];

const retiredImportFragments = [
  '/AppHeader',
  '/AddNodeDialog',
  '/PublishTemplateDialog',
  '/ShareWorkflowDialog',
  '/TemplateMarketDialog',
  '/anchored-context-menu',
  '/TemplateCard',
  '/templateNode',
  '/ChatFileMediaPreview',
  '/ChatFileViewer',
  '/ChatPythonCodeEditor',
  '/grid-edit-history',
  '/use-virtual-grid-rows',
];

async function exists(path) {
  try {
    await access(path, constants.F_OK);
    return true;
  } catch {
    return false;
  }
}

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) files.push(...await sourceFiles(path));
    else if (/\.(?:ts|tsx)$/.test(entry.name)) files.push(path);
  }
  return files;
}

const errors = [];
for (const path of retiredPaths) {
  if (await exists(join(root, path))) errors.push(`retired UI file still exists: ${path}`);
}

for (const file of await sourceFiles(srcRoot)) {
  const source = await readFile(file, 'utf8');
  for (const fragment of retiredImportFragments) {
    if (source.includes(fragment)) {
      errors.push(`${relative(root, file)} still references retired module ${fragment}`);
    }
  }
}

// Browser authentication is cookie-only. Keep every browser test fixture from
// silently restoring the retired localStorage/Bearer-token transport and
// producing a false-positive acceptance run. Negative assertions/removals are
// intentionally allowed; these patterns only match writes and header values.
const retiredAuthPatterns = [
  {
    pattern: /Authorization\s*:\s*[`'"]Bearer\b/,
    message: 'acceptance fixture still sends a Bearer authorization header',
  },
  {
    pattern: /localStorage\.setItem\(\s*['"]vibecanvas\.token['"]/,
    message: 'acceptance fixture still persists a session token in localStorage',
  },
];
for (const file of await sourceFiles(join(root, 'e2e'))) {
  const source = await readFile(file, 'utf8');
  for (const { pattern, message } of retiredAuthPatterns) {
    if (pattern.test(source)) errors.push(`${relative(root, file)}: ${message}`);
  }
}

if (errors.length) {
  console.error(errors.join('\n'));
  process.exit(1);
}

console.log(`Retired UI guard passed: ${retiredPaths.length} obsolete modules remain absent.`);
