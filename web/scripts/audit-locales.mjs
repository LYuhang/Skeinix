import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const localeDirectory = resolve('src/lib/i18n/locales');
const en = JSON.parse(await readFile(resolve(localeDirectory, 'en.json'), 'utf8'));
const zh = JSON.parse(await readFile(resolve(localeDirectory, 'zh.json'), 'utf8'));

const enKeys = new Set(Object.keys(en));
const zhKeys = new Set(Object.keys(zh));
const failures = [];
// These legacy hints document the user-authored template syntax itself; their
// double braces are examples, not i18next interpolation parameters.
const literalTemplateExampleKeys = new Set(['prompt_hint', 'template_hint']);
const interpolationTokens = (value) => [
  ...String(value).matchAll(/{{\s*([\w.-]+)/g),
].map((match) => match[1]).sort();

for (const key of [...enKeys].sort()) {
  if (!zhKeys.has(key)) failures.push(`zh locale is missing: ${key}`);
  if (typeof en[key] !== 'string' || !en[key].trim()) {
    failures.push(`en locale is empty or not a string: ${key}`);
  }
  if (
    zhKeys.has(key)
    && !literalTemplateExampleKeys.has(key)
    && typeof en[key] === 'string'
    && typeof zh[key] === 'string'
  ) {
    const enTokens = interpolationTokens(en[key]);
    const zhTokens = interpolationTokens(zh[key]);
    if (enTokens.join('\0') !== zhTokens.join('\0')) {
      failures.push(
        `locale interpolation mismatch: ${key} (en: ${enTokens.join(', ') || 'none'}; zh: ${zhTokens.join(', ') || 'none'})`,
      );
    }
  }
}
for (const key of [...zhKeys].sort()) {
  if (!enKeys.has(key)) failures.push(`en locale is missing: ${key}`);
  if (typeof zh[key] !== 'string' || !zh[key].trim()) {
    failures.push(`zh locale is empty or not a string: ${key}`);
  }
}

if (failures.length) {
  process.stderr.write(`${failures.join('\n')}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(
    `Locale parity passed: ${enKeys.size} English and Simplified Chinese keys.\n`,
  );
}
