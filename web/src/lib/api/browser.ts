// Browser-automation: mint a short-lived, scoped token for the extension (§15.A).
// The HttpOnly Session authorizes minting; only the narrow capability is
// handed to the extension WebSocket transport.
import { getApiBase } from '@/lib/base-path';
import { sessionFetch } from '@/lib/api/session-fetch';

export async function mintBrowserToken(wfId: string, browserId: string): Promise<string> {
  const base = getApiBase();
  const r = await sessionFetch(`${base}/api/v1/browser/token`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ wf_id: wfId, browser_id: browserId }),
  });
  if (!r.ok) throw new Error(`browser token mint failed: ${r.status}`);
  return (await r.json()).token as string;
}
