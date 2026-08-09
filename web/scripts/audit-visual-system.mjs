import { readdir, readFile } from 'node:fs/promises';
import { join, relative, resolve } from 'node:path';

const webRoot = resolve(process.cwd(), 'src');

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(entries.map(async (entry) => {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(?:tsx|css)$/.test(entry.name) && !/\.test\.|\.spec\./.test(entry.name) ? [path] : [];
  }));
  return files.flat();
}

const forbidden = [
  { label: '10 px functional text', pattern: /text-\[10px\]|text-tiny/g },
  { label: '11 px functional text (12 px is the minimum meta size)', pattern: /text-\[11px\]/g },
  { label: 'broad transition-all', pattern: /\btransition-all\b/g },
  { label: 'native confirm dialog', pattern: /window\.confirm\s*\(|(?<![\w.])confirm\s*\(/g },
  { label: 'page-local raw color literal', pattern: /['"`]#[0-9a-fA-F]{3,8}['"`]/g, pageOnly: true },
  { label: 'legacy blocking file-view geometry', pattern: /(?:48|72)vw|min-w-\[680px\]|shadow-2xl/g },
];

const violations = [];
for (const file of await sourceFiles(webRoot)) {
  const source = await readFile(file, 'utf8');
  for (const rule of forbidden) {
    const localPath = relative(process.cwd(), file);
    if (rule.pageOnly && !/^src\/(?:pages|components)\//.test(localPath)) continue;
    for (const match of source.matchAll(rule.pattern)) {
      const line = source.slice(0, match.index).split('\n').length;
      violations.push(`${localPath}:${line}: ${rule.label}`);
    }
  }
}

const manifestPath = resolve(process.cwd(), '../extension/manifest.json');
const manifest = JSON.parse(await readFile(manifestPath, 'utf8'));
const injectedScripts = (manifest.content_scripts ?? []).flatMap((entry) => entry.js ?? []);
if (injectedScripts.length !== 1 || injectedScripts[0] !== 'island/content.js') {
  violations.push('../extension/manifest.json: browser control must inject Dynamic Island only');
}

if (violations.length) {
  process.stderr.write(`${violations.join('\n')}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write('Visual-system guardrails passed.\n');
}
