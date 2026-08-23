import assert from 'node:assert/strict';
import { test } from 'node:test';

import { collectLiteralTranslationCalls } from './audit-locale-keys.mjs';

test('collects only literal t() and i18n.t() keys with source lines', () => {
  const calls = collectLiteralTranslationCalls([
    "const first = t('chat.ready');",
    'const ignored = t(dynamicKey);',
    "const second = i18n.t(`settings.saved`);",
    "const unrelated = client.t('not.translation');",
  ].join('\n'));

  assert.deepEqual(calls, [
    { key: 'chat.ready', line: 1 },
    { key: 'settings.saved', line: 3 },
  ]);
});

test('does not inspect comments or template expressions', () => {
  const calls = collectLiteralTranslationCalls([
    "// t('comment.example')",
    "const dynamic = t(`resource.${kind}`);",
  ].join('\n'));

  assert.deepEqual(calls, []);
});
