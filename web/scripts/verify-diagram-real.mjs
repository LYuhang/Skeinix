import { spawn } from 'node:child_process';
import { readdirSync, statSync } from 'node:fs';
import { delimiter, resolve } from 'node:path';

const args = process.argv.slice(2);
const explicitIndex = args.indexOf('--evidence-dir');
let evidenceDir;
if (explicitIndex >= 0) {
  evidenceDir = resolve(args[explicitIndex + 1]);
} else {
  const runsRoot = resolve('test-results', 'diagram-acceptance-runs');
  const latest = readdirSync(runsRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => ({
      name: entry.name,
      mtime: statSync(resolve(runsRoot, entry.name)).mtimeMs,
    }))
    .sort((left, right) => right.mtime - left.mtime)[0];
  if (!latest) throw new Error('No Diagram acceptance run exists');
  evidenceDir = resolve(runsRoot, latest.name);
}

const repoRoot = resolve('..');
const python = process.env.VIBECANVAS_PYTHON ?? 'python';
const pythonPath = [
  resolve(repoRoot, 'api/src'),
  resolve(repoRoot, 'engine/src'),
  process.env.PYTHONPATH,
].filter(Boolean).join(delimiter);
const reportDir = resolve('test-results', 'diagram-acceptance-report');

const child = spawn(python, [
  '-m', 'vibecanvas_api.diagrams.acceptance',
  'verify',
  '--fixtures', resolve('e2e/fixtures/diagram-acceptance.json'),
  '--evidence-dir', evidenceDir,
  '--report-dir', reportDir,
], {
  stdio: 'inherit',
  env: { ...process.env, PYTHONPATH: pythonPath },
});
child.on('error', (error) => {
  console.error(`Unable to start Diagram evidence verifier: ${error.message}`);
  process.exitCode = 1;
});
child.on('exit', (code, signal) => {
  if (signal) console.error(`Diagram evidence verifier terminated by ${signal}`);
  process.exitCode = code ?? 1;
});
