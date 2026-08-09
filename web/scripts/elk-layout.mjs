import process from 'node:process';

import ELK from 'elkjs/lib/elk.bundled.js';

const MAX_INPUT_BYTES = 2 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 8 * 1024 * 1024;
const ENGINE_VERSION = 'elkjs-0.12.0';

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  input += chunk;
  if (Buffer.byteLength(input) > MAX_INPUT_BYTES) {
    process.stderr.write('ELK input exceeds the 2 MiB limit.\n');
    process.exit(64);
  }
});

process.stdin.on('end', async () => {
  try {
    const request = JSON.parse(input);
    if (!request || typeof request !== 'object' || !request.graph) {
      throw new Error('Expected an object containing an ELK graph.');
    }
    const elk = new ELK({
      algorithms: ['layered'],
      defaultLayoutOptions: request.layoutOptions ?? {},
    });
    const graph = await elk.layout(request.graph);
    const response = JSON.stringify({ engineVersion: ENGINE_VERSION, graph });
    if (Buffer.byteLength(response) > MAX_OUTPUT_BYTES) {
      throw new Error('ELK output exceeds the 8 MiB limit.');
    }
    process.stdout.write(response);
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`);
    process.exitCode = 65;
  }
});
