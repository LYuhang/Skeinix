import { spawnSync } from 'node:child_process';

const forwardedArgs = process.argv.slice(2);
const pnpm = process.platform === 'win32' ? 'pnpm.cmd' : 'pnpm';

function runVitest(args) {
  const result = spawnSync(pnpm, ['exec', 'vitest', 'run', ...args], {
    stdio: 'inherit',
  });
  if (result.error) throw result.error;
  return result.status ?? 1;
}

// A targeted invocation must stay fast and preserve the long-standing
// `pnpm test path/to/file.test.tsx` developer workflow. The repository-wide
// suite uses fresh processes because retaining 140+ isolated jsdom module
// graphs in one Vitest worker can prevent shutdown and can leak test-only
// global state across late files.
if (forwardedArgs.length > 0) {
  process.exit(runVitest(forwardedArgs));
}

const shardCount = 4;
for (let shard = 1; shard <= shardCount; shard += 1) {
  const status = runVitest([`--shard=${shard}/${shardCount}`]);
  if (status !== 0) process.exit(status);
}
