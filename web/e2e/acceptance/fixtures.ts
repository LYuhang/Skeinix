/**
 * Acceptance-suite fixtures (2026-06-09 workflow E2E acceptance).
 *
 * The legacy suite authenticated with a Bearer token copied into Web Storage.
 * Secure Cookie is now the only browser Session path: registration captures
 * the HttpOnly Session cookie plus the readable CSRF cookie, direct API calls
 * use Cookie + Origin + X-CSRF-Token, and the browser receives cookies without
 * ever exposing the Session credential to JavaScript.
 *
 * Existing specs still pass `RealUser.token`, but it is only an opaque,
 * process-local lookup handle for a cookie jar. It is never sent on the wire.
 *
 * SCREENSHOTS: `screenshot(page, name)` writes a full-page PNG to
 * `web/screenshots/acc-<name>.png` on SUCCESS (proof-of-working), not the
 * Playwright default of only-on-failure.
 */
import type { BrowserContext, Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const API_BASE =
  process.env.VIBECANVAS_API_BASE ??
  `http://${process.env.VIBECANVAS_E2E_HOST ?? '127.0.0.1'}:${process.env.VIBECANVAS_API_PORT ?? 8000}`;
const WEB_ORIGIN =
  process.env.VIBECANVAS_WEB_ORIGIN ??
  `http://${process.env.VIBECANVAS_E2E_HOST ?? '127.0.0.1'}:${process.env.VIBECANVAS_WEB_PORT ?? 5173}`;

const __dirname = path.dirname(fileURLToPath(import.meta.url));
// web/e2e/acceptance → web/screenshots
const SCREENSHOT_DIR = path.resolve(__dirname, '..', '..', 'screenshots');

export interface RealUser {
  /** Opaque cookie-jar handle retained for backwards-compatible spec APIs. */
  token: string;
  email: string;
  userId: string;
}

let counter = 0;
interface CookieMaterial {
  cookies: Array<{ name: string; value: string }>;
}

const materialByHandle = new Map<string, CookieMaterial>();

function responseCookies(response: Response): Array<{ name: string; value: string }> {
  return response.headers.getSetCookie().flatMap((header) => {
    const pair = header.split(';', 1)[0] ?? '';
    const separator = pair.indexOf('=');
    return separator > 0
      ? [{ name: pair.slice(0, separator), value: pair.slice(separator + 1) }]
      : [];
  });
}

/**
 * Register a brand-new user and retain its Secure Cookie material only in the
 * Playwright worker. Each call uses a process-unique email so specs never
 * collide on the unique-email constraint.
 */
export async function registerRealUser(): Promise<RealUser> {
  const useTestUser = process.env.VIBECANVAS_E2E_USE_TEST_USER === '1';
  const email = `acc-${Date.now()}-${counter++}-${Math.floor(
    Math.random() * 1e6,
  )}@example.com`;
  const password = 'Passw0rd!123';
  const username = email.slice(0, email.indexOf('@'));
  const res = await fetch(`${API_BASE}/api/v1/auth/${useTestUser ? 'login' : 'register'}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Origin: WEB_ORIGIN },
    body: JSON.stringify(useTestUser
      ? { email: 'test', password: 'test' }
      : { email, username, password }),
  });
  if (!res.ok) {
    throw new Error(`acceptance login failed: ${res.status} ${await res.text()}`);
  }
  const json = (await res.json()) as {
    user: { user_id: string; email: string };
  };
  const cookies = responseCookies(res);
  if (!cookies.some((cookie) => cookie.name.endsWith('vibecanvas-web-session'))) {
    throw new Error('register succeeded without a Secure Session cookie');
  }
  const handle = crypto.randomUUID();
  materialByHandle.set(handle, { cookies });
  return { token: handle, email: json.user.email, userId: json.user.user_id };
}

function authHeaders(token: string): Record<string, string> {
  const material = materialByHandle.get(token);
  if (!material) throw new Error('unknown acceptance Session handle');
  const csrf = material.cookies.find((cookie) => (
    cookie.name.endsWith('vibecanvas-web-csrf')
  ))?.value;
  return {
    'Content-Type': 'application/json',
    Origin: WEB_ORIGIN,
    Cookie: material.cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; '),
    ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
  };
}

/**
 * Seed Secure Cookies and the initial locale before page scripts run. The
 * Session cookie remains HttpOnly and no auth value is written to Web Storage.
 */
export async function seedAuth(
  context: BrowserContext,
  token: string,
  locale = 'en',
): Promise<void> {
  const material = materialByHandle.get(token);
  if (!material) throw new Error('unknown acceptance Session handle');
  await context.addCookies(material.cookies.map((cookie) => ({
    ...cookie,
    url: WEB_ORIGIN,
  })));
  await context.addInitScript(
    (lng) => {
      if (window.localStorage.getItem('vibecanvas.locale') === null) {
        window.localStorage.setItem('vibecanvas.locale', lng);
      }
    },
    locale,
  );
}

/** Create a workflow via the backend. Returns `wf_id`. */
export async function createWorkflow(
  token: string,
  name: string,
): Promise<string> {
  const res = await fetch(`${API_BASE}/api/v1/workflows`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ name, description: '', tags: [] }),
  });
  if (!res.ok) {
    throw new Error(`createWorkflow failed: ${res.status} ${await res.text()}`);
  }
  return ((await res.json()) as { wf_id: string }).wf_id;
}

/**
 * Commit a full workflow dict server-side (the same endpoint the canvas
 * Save button hits). This is the robust seed path — the `/edits` endpoint
 * takes the JSON-Patch op shape the frontend now emits, but for fixtures we
 * just want to plant a known graph, so a full commit is simpler + format-
 * stable.
 */
export async function commitWorkflow(
  token: string,
  wfId: string,
  workflow: Record<string, unknown>,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/workflows/${wfId}/commits`, {
    method: 'POST',
    headers: authHeaders(token),
    body: JSON.stringify({ workflow, note: 'acceptance seed' }),
  });
  if (!res.ok) {
    throw new Error(`commitWorkflow failed: ${res.status} ${await res.text()}`);
  }
}

/**
 * Seed a known graph server-side from a list of node dicts. Convenience so a
 * spec can open the canvas + inspector against a deterministic shape rather
 * than hand-building it through the UI (used by the inspector / execution /
 * check cases that are not themselves about building).
 */
export async function seedNodes(
  token: string,
  wfId: string,
  nodes: Record<string, unknown>[],
): Promise<void> {
  const workflow: Record<string, unknown> = {};
  for (const n of nodes) {
    workflow[n.node_id as string] = n;
  }
  await commitWorkflow(token, wfId, workflow);
}

/**
 * A minimal CodeNode-only runnable workflow: Start → Code → End.
 *
 * NOTE the engine's jsonschema constraints (engine/.../nodes/start.py,end.py):
 *   - StartNode.node_name MUST be `__start__`; EndNode.node_name MUST be
 *     `__end__`. References use `__start__.field` / `<node_name>.field`.
 * Getting these wrong makes the authoritative backend Check reject the graph.
 */
export function codeOnlyWorkflowNodes(): Record<string, unknown>[] {
  return [
    {
      node_id: 'node_1',
      node_name: '__start__',
      node_type: 'StartNode',
      node_description: '',
      input_fields: {},
      output_fields: {},
      node_config: {},
      children: ['node_2'],
      __attributes__: { x: 0, y: 0 },
    },
    {
      node_id: 'node_2',
      node_name: 'Compute',
      node_type: 'CodeNode',
      node_description: '',
      input_fields: {},
      output_fields: { result: { type: 'string', description: 'computed' } },
      node_config: {
        programming_language: 'python',
        process_fn: 'def process_fn(inputs):\n    return {"result": "hello-from-code"}',
      },
      children: ['node_3'],
      __attributes__: { x: 320, y: 0 },
    },
    {
      node_id: 'node_3',
      node_name: '__end__',
      node_type: 'EndNode',
      node_description: '',
      // EndNode: output_fields MUST mirror input_fields exactly (same names +
      // types) per engine/.../nodes/end.py.
      input_fields: { result: { type: 'string', value: '', reference: 'Compute.result' } },
      output_fields: { result: { type: 'string', description: 'final' } },
      node_config: {},
      children: [],
      __attributes__: { x: 640, y: 0 },
    },
  ];
}

/** Full-page screenshot to `web/screenshots/acc-<name>.png` (proof-of-working). */
export async function screenshot(page: Page, name: string): Promise<void> {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `acc-${name}.png`),
    fullPage: true,
  });
}
