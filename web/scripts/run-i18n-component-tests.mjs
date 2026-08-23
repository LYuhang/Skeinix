import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const manifest = JSON.parse(await readFile(
  resolve('e2e/fixtures/i18n-surface-inventory.json'),
  'utf8',
));
const specs = manifest.componentSpecs ?? [];

if (!specs.length) {
  process.stderr.write('The i18n component-spec matrix is empty.\n');
  process.exitCode = 1;
} else {
  const command = process.platform === 'win32' ? 'pnpm.cmd' : 'pnpm';
  const child = spawn(command, ['exec', 'vitest', 'run', ...specs], {
    cwd: process.cwd(),
    env: process.env,
    stdio: 'inherit',
  });
  child.on('error', (error) => {
    process.stderr.write(`Could not start the i18n component matrix: ${error.message}\n`);
    process.exitCode = 1;
  });
  child.on('exit', (code, signal) => {
    if (signal) {
      process.stderr.write(`i18n component matrix terminated by ${signal}.\n`);
      process.exitCode = 1;
      return;
    }
    process.exitCode = code ?? 1;
  });
}
