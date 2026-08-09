/** Parse turn-local commands that alter transport routing. */
export function parseAgentCommand(
  raw: string,
): { mode: 'chat' | 'browser'; content: string } {
  const browser = raw.match(/^\/browser(?:\s+([\s\S]*))?$/);
  if (browser) return { mode: 'browser', content: (browser[1] ?? '').trim() };
  return { mode: 'chat', content: raw };
}
