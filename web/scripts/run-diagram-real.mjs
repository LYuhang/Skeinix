import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { relative, resolve } from 'node:path';

const stamp = new Date().toISOString().replaceAll(/[:.]/g, '-');
const outputDir = resolve('test-results', 'diagram-acceptance-runs', stamp);
mkdirSync(outputDir, { recursive: true });
const repoRoot = resolve('..');

const fingerprintRoots = [
  'api/src/vibecanvas_api/diagrams',
  'api/src/vibecanvas_api/routes/previews.py',
  'api/src/vibecanvas_api/services/platform_mcp/diagram_tools',
  'web/e2e/37-diagram-real-acceptance.spec.ts',
  'web/e2e/fixtures/diagram-acceptance.json',
  'web/scripts/run-diagram-real.mjs',
  'web/scripts/verify-diagram-real.mjs',
  'web/src/lib/preview',
  'web/src/pages/chat/preview',
];

function sourceFiles(target) {
  const absolute = resolve(repoRoot, target);
  if (!statSync(absolute).isDirectory()) return [absolute];
  return readdirSync(absolute, { withFileTypes: true })
    .filter((entry) => entry.name !== '__pycache__')
    .flatMap((entry) => sourceFiles(relative(repoRoot, resolve(absolute, entry.name))));
}

const sourceHash = createHash('sha256');
for (const file of fingerprintRoots.flatMap(sourceFiles).sort()) {
  sourceHash.update(relative(repoRoot, file));
  sourceHash.update('\0');
  sourceHash.update(readFileSync(file));
  sourceHash.update('\0');
}
const sourceFingerprint = `sha256:${sourceHash.digest('hex')}`;
const forwardedArgs = process.argv.slice(2);
if (forwardedArgs[0] === '--') forwardedArgs.shift();
// The real acceptance suite owns browser traffic and evidence only. Service
// lifecycle is deliberately owned by launch.sh so the run cannot accidentally
// boot a second API/Web stack (or a Vite watcher) beside the stack under test.
const acceptanceWebPort = process.env.VIBECANVAS_WEB_PORT
  ?? process.env.WEB_PORT
  ?? '9001';

const child = spawn(
  'pnpm',
  [
    'exec',
    'playwright',
    'test',
    'e2e/37-diagram-real-acceptance.spec.ts',
    '--workers=1',
    `--output=${outputDir}`,
    ...forwardedArgs,
  ],
  {
    stdio: 'inherit',
    env: {
      ...process.env,
      DIAGRAM_ACCEPTANCE_RUN_ID: stamp,
      DIAGRAM_ACCEPTANCE_SOURCE_FINGERPRINT: sourceFingerprint,
      VIBECANVAS_SKIP_WEB_SERVER: '1',
      VIBECANVAS_WEB_PORT: acceptanceWebPort,
    },
  },
);

child.on('error', (error) => {
  console.error(`Unable to start Diagram acceptance: ${error.message}`);
  process.exitCode = 1;
});
child.on('exit', (code, signal) => {
  if (signal) {
    console.error(`Diagram acceptance terminated by ${signal}`);
    process.exitCode = 1;
    return;
  }
  if (code !== 0) {
    process.exitCode = code ?? 1;
    return;
  }
  const verify = spawn(
    'node',
    ['scripts/verify-diagram-real.mjs', '--evidence-dir', outputDir],
    { stdio: 'inherit', env: process.env },
  );
  verify.on('error', (error) => {
    console.error(`Unable to verify Diagram acceptance: ${error.message}`);
    process.exitCode = 1;
  });
  verify.on('exit', (verifyCode, verifySignal) => {
    if (verifySignal) console.error(`Diagram verification terminated by ${verifySignal}`);
    process.exitCode = verifyCode ?? 1;
  });
});
