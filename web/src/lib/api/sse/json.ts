export function isSseDoneSentinel(data: string | undefined | null): boolean {
  return (data ?? '').trim() === '[DONE]';
}

export function parseSseJson(data: string | undefined | null): unknown {
  if (!data || isSseDoneSentinel(data)) return {};
  return JSON.parse(data);
}
