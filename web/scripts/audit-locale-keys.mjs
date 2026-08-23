import { readFile, readdir } from 'node:fs/promises';
import { relative, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

import ts from 'typescript';

const SOURCE_ROOT = resolve('src');
const LOCALE_ROOT = resolve('src/lib/i18n/locales');

function translationCall(node) {
  if (!ts.isCallExpression(node)) return false;
  if (ts.isIdentifier(node.expression)) return node.expression.text === 't';
  return ts.isPropertyAccessExpression(node.expression)
    && ts.isIdentifier(node.expression.expression)
    && node.expression.expression.text === 'i18n'
    && node.expression.name.text === 't';
}

function literalKey(argument) {
  return argument && ts.isStringLiteralLike(argument) ? argument.text : null;
}

export function collectLiteralTranslationCalls(sourceText, file = 'source.tsx') {
  const source = ts.createSourceFile(
    file,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const calls = [];
  function visit(node) {
    if (translationCall(node)) {
      const key = literalKey(node.arguments[0]);
      if (key) {
        const position = source.getLineAndCharacterOfPosition(node.getStart(source));
        calls.push({ key, line: position.line + 1 });
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
  return calls;
}

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === '__tests__') continue;
      files.push(...await sourceFiles(path));
    } else if (/\.tsx?$/.test(entry.name) && !/(?:\.test|\.spec|schema\.d)\.tsx?$/.test(entry.name)) {
      files.push(path);
    }
  }
  return files;
}

export async function auditLocaleKeys({
  sourceRoot = SOURCE_ROOT,
  localeRoot = LOCALE_ROOT,
} = {}) {
  const [en, zh] = await Promise.all([
    readFile(resolve(localeRoot, 'en.json'), 'utf8').then(JSON.parse),
    readFile(resolve(localeRoot, 'zh.json'), 'utf8').then(JSON.parse),
  ]);
  const calls = [];
  for (const file of await sourceFiles(sourceRoot)) {
    for (const call of collectLiteralTranslationCalls(await readFile(file, 'utf8'), file)) {
      calls.push({
        ...call,
        path: relative(resolve('.'), file).replaceAll('\\', '/'),
      });
    }
  }
  const missing = calls.filter(({ key }) => !(key in en) || !(key in zh));
  return { calls, missing };
}

async function main() {
  const { calls, missing } = await auditLocaleKeys();
  if (missing.length) {
    process.stderr.write('Literal translation keys missing from a locale catalog:\n');
    for (const item of missing) {
      process.stderr.write(`  ${item.path}:${item.line} ${item.key}\n`);
    }
    process.exitCode = 1;
    return;
  }
  process.stdout.write(
    `Locale key coverage passed: ${new Set(calls.map(({ key }) => key)).size} literal key(s).\n`,
  );
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  await main();
}
