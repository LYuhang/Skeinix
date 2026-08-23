import { readFile, readdir } from 'node:fs/promises';
import { relative, resolve } from 'node:path';
import ts from 'typescript';

const ROOT = resolve('src');
const ALLOWLIST_PATH = resolve('scripts/i18n-copy-allowlist.json');
const USER_FACING_ATTRIBUTES = new Set([
  'alt',
  'aria-label',
  'aria-placeholder',
  'label',
  'placeholder',
  'title',
]);
const USER_FEEDBACK_METHODS = new Set([
  'toast.error',
  'toast.info',
  'toast.success',
  'toast.warning',
]);

function normalizeText(value) {
  return value.replace(/\s+/g, ' ').trim();
}

function isUserCopy(value) {
  const text = normalizeText(value);
  if (text.length <= 1 || !/[A-Za-z\u3400-\u9fff]/u.test(text)) return false;
  if (/^(?:https?:\/\/|\/|\.|\{|\[)/.test(text)) return false;
  if (/[{}_]|\b(?:api_key|api_url|max_tokens|top_[kp]|process_fn|node_id)\b/.test(text)) {
    return false;
  }
  if (/^(?:cURL|Python|JavaScript|HTTP|CSV|JSONL|Excel \(\.xlsx\)|PDF|PNG|JPG|SVG|SCIM|IdP|OIDC \+ SCIM|Chrome|Codex|LangChain|OpenRouter|SKILL\.md|P50|P95|ms|sv|tok)$/i.test(text)) {
    return false;
  }
  // Bare schema keys and product identifiers are not natural-language copy.
  if (!/\s|[\u3400-\u9fff]/u.test(text) && /^[\w.-]+$/.test(text)) return false;
  return true;
}

async function sourceFiles(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = [];
  for (const entry of entries) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === '__tests__') continue;
      files.push(...await sourceFiles(path));
      continue;
    }
    if (!/\.tsx?$/.test(entry.name) || /(?:\.test|\.spec|schema\.d)\.tsx?$/.test(entry.name)) {
      continue;
    }
    files.push(path);
  }
  return files;
}

function callName(expression) {
  if (!ts.isPropertyAccessExpression(expression)) return '';
  return `${expression.expression.getText()}.${expression.name.getText()}`;
}

function finding(file, source, node, kind, value) {
  const position = source.getLineAndCharacterOfPosition(node.getStart(source));
  return {
    path: relative(resolve('.'), file).replaceAll('\\', '/'),
    line: position.line + 1,
    kind,
    text: normalizeText(value),
  };
}

function inspect(file, sourceText) {
  const source = ts.createSourceFile(
    file,
    sourceText,
    ts.ScriptTarget.Latest,
    true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const findings = [];

  function visit(node) {
    if (ts.isJsxText(node) && isUserCopy(node.text)) {
      findings.push(finding(file, source, node, 'jsx-text', node.text));
    }

    if (
      ts.isJsxAttribute(node)
      && USER_FACING_ATTRIBUTES.has(node.name.getText(source))
      && node.initializer
      && ts.isStringLiteral(node.initializer)
      && isUserCopy(node.initializer.text)
    ) {
      findings.push(finding(
        file,
        source,
        node,
        `jsx-${node.name.getText(source)}`,
        node.initializer.text,
      ));
    }

    if (
      ts.isCallExpression(node)
      && USER_FEEDBACK_METHODS.has(callName(node.expression))
      && node.arguments[0]
      && ts.isStringLiteralLike(node.arguments[0])
      && isUserCopy(node.arguments[0].text)
    ) {
      findings.push(finding(
        file,
        source,
        node.arguments[0],
        `feedback-${callName(node.expression)}`,
        node.arguments[0].text,
      ));
    }
    ts.forEachChild(node, visit);
  }

  visit(source);
  return findings;
}

const allowlist = JSON.parse(await readFile(ALLOWLIST_PATH, 'utf8'));
for (const entry of allowlist) {
  if (!entry.path || !entry.kind || !entry.text || !entry.reason?.trim()) {
    throw new Error('Every i18n copy allowlist entry requires path, kind, text, and reason.');
  }
}
const allowed = new Map(allowlist.map((entry) => [
  `${entry.path}\u0000${entry.kind}\u0000${entry.text}`,
  entry,
]));
const findings = [];
for (const file of await sourceFiles(ROOT)) {
  findings.push(...inspect(file, await readFile(file, 'utf8')));
}

const unexpected = findings.filter((item) => !allowed.has(
  `${item.path}\u0000${item.kind}\u0000${item.text}`,
));
const stale = allowlist.filter((entry) => !findings.some((item) =>
  item.path === entry.path && item.kind === entry.kind && item.text === entry.text,
));

if (unexpected.length || stale.length) {
  if (unexpected.length) {
    process.stderr.write('Untranslated high-confidence user-facing copy:\n');
    for (const item of unexpected) {
      process.stderr.write(`  ${item.path}:${item.line} [${item.kind}] ${JSON.stringify(item.text)}\n`);
    }
  }
  if (stale.length) {
    process.stderr.write('Stale i18n copy allowlist entries:\n');
    for (const item of stale) {
      process.stderr.write(`  ${item.path} [${item.kind}] ${JSON.stringify(item.text)}\n`);
    }
  }
  process.exitCode = 1;
} else {
  process.stdout.write(
    `Hard-coded copy audit passed: ${findings.length} reviewed baseline finding(s).\n`,
  );
}
