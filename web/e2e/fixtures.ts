/**
 * E2E test fixtures (P3.1 T7).
 *
 * Seed-data helpers that hit the FastAPI backend directly to create and
 * tear down workflows / chats / nodes for the Playwright journey specs.
 * Tests own their fixtures: each `test.beforeAll` creates what it needs,
 * `test.afterAll` cleans up. There is intentionally no global seed
 * because journey specs mutate the database (create / delete / vibe);
 * a shared fixture would couple them.
 *
 * Auth: Secure Cookie is the only browser Session path. We register/login a
 * real user once per worker process, retain its HttpOnly Session + readable
 * CSRF cookies in a process-local jar, and expose only an opaque handle to
 * specs. Direct API calls use Cookie + Origin + X-CSRF-Token; no Session value
 * is copied into Web Storage or an Authorization header. The API fixture user
 * therefore remains identical to the browser user without weakening the
 * production authentication model (or creating a vacuous-green RLS test).
 *
 * API base: `VIBECANVAS_API_BASE` env (matches the backend `webServer`
 * port). Defaults to localhost:8000.
 */

const API_BASE =
  process.env.VIBECANVAS_API_BASE ??
  `http://${process.env.VIBECANVAS_E2E_HOST ?? '127.0.0.1'}:${process.env.VIBECANVAS_API_PORT ?? 8000}`;
const WEB_ORIGIN =
  process.env.VIBECANVAS_WEB_ORIGIN ??
  `http://${process.env.VIBECANVAS_E2E_HOST ?? '127.0.0.1'}:${process.env.VIBECANVAS_WEB_PORT ?? 5173}`;

interface E2EAuthMaterial {
  handle: string;
  cookies: Array<{ name: string; value: string }>;
}

// Lazy, register-once-per-worker Secure Cookie Session.
let _authPromise: Promise<E2EAuthMaterial> | null = null;
const materialByHandle = new Map<string, E2EAuthMaterial>();

async function registerE2EAuthMaterial(): Promise<E2EAuthMaterial> {
  const email = `e2e_${Date.now()}_${Math.random().toString(36).slice(2, 10)}@example.com`;
  const username = email.slice(0, email.indexOf('@'));
  const useTestUser = process.env.VIBECANVAS_E2E_USE_TEST_USER === '1';
  const res = await fetch(`${API_BASE}/api/v1/auth/${useTestUser ? 'login' : 'register'}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Origin: WEB_ORIGIN,
    },
    body: JSON.stringify(useTestUser
      ? { email: 'test', password: 'test' }
      : { email, username, password: 'pw-123456' }),
  });
  if (!res.ok) {
    throw new Error(`register failed: ${res.status} ${await res.text()}`);
  }
  await res.json();
  const getSetCookie = (res.headers as Headers & { getSetCookie?: () => string[] }).getSetCookie;
  const cookies = (getSetCookie?.call(res.headers) ?? []).flatMap((header) => {
    const pair = header.split(';', 1)[0] ?? '';
    const separator = pair.indexOf('=');
    return separator > 0
      ? [{ name: pair.slice(0, separator), value: pair.slice(separator + 1) }]
      : [];
  });
  const sessionCookie = cookies.find((cookie) => cookie.name.endsWith('-session'));
  const csrfCookie = cookies.find((cookie) => cookie.name.endsWith('-csrf'));
  if (!sessionCookie || !csrfCookie) {
    throw new Error('register succeeded without Secure Session and CSRF cookies');
  }
  const handle = crypto.randomUUID();
  const material = {
    handle,
    cookies,
  } satisfies E2EAuthMaterial;
  materialByHandle.set(handle, material);
  return material;
}

/** Return an opaque process-local handle for an isolated Secure Cookie jar. */
export async function registerE2EUserToken(): Promise<string> {
  const material = await registerE2EAuthMaterial();
  return material.handle;
}

/** Headers for direct API calls owned by an explicitly registered E2E Session. */
export function e2eSessionHeaders(session: string): Record<string, string> {
  const material = materialByHandle.get(session);
  if (!material) throw new Error('unknown E2E Session handle');
  const csrf = material.cookies.find((cookie) => cookie.name.endsWith('-csrf'))?.value;
  return {
    'Content-Type': 'application/json',
    Origin: WEB_ORIGIN,
    Cookie: material.cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; '),
    ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
  };
}

async function getAuthMaterial(): Promise<E2EAuthMaterial> {
  if (_authPromise) return _authPromise;
  _authPromise = registerE2EAuthMaterial();
  return _authPromise;
}

async function authHeaders(): Promise<Record<string, string>> {
  const material = await getAuthMaterial();
  const csrf = material.cookies.find((cookie) => cookie.name.endsWith('-csrf'))?.value;
  return {
    'Content-Type': 'application/json',
    Origin: WEB_ORIGIN,
    Cookie: material.cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join('; '),
    ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
  };
}

/**
 * Create a fresh workflow via the backend. Returns `wf_id`. The new
 * workflow has no nodes; use `seedStartNode` if a journey needs one.
 */
export async function createWorkflow(name: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/v1/workflows`, {
    method: 'POST',
    headers: await authHeaders(),
    body: JSON.stringify({ name, description: '', tags: [] }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`createWorkflow failed: ${res.status} ${body}`);
  }
  const meta = (await res.json()) as { wf_id: string };
  return meta.wf_id;
}

/** Delete a workflow by id. Idempotent — 404 is treated as success. */
export async function deleteWorkflow(wfId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/workflows/${wfId}`, {
    method: 'DELETE',
    headers: await authHeaders(),
  });
  if (!res.ok && res.status !== 404) {
    const body = await res.text();
    throw new Error(`deleteWorkflow failed: ${res.status} ${body}`);
  }
}

/**
 * Apply a vibe-ops update list. Hits POST /workflows/{wf_id}/edits,
 * which validates ops and bumps the subversion on success.
 */
export async function applyEdits(wfId: string, updates: unknown[][]): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/workflows/${wfId}/edits`, {
    method: 'POST',
    headers: await authHeaders(),
    body: JSON.stringify({ updates }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`applyEdits failed: ${res.status} ${body}`);
  }
  const result = (await res.json()) as { first_error?: string | null };
  if (result.first_error) {
    throw new Error(`applyEdits returned error: ${result.first_error}`);
  }
}

/**
 * Seed a minimal StartNode at canvas origin. Required for executions —
 * `Workflow.check` enforces exactly one StartNode and full reachability
 * before any node can run.
 *
 * Mirrors `BaseNode.GENERAL_NODE_SCHEMA`: every node carries the same
 * shape (node_id / node_name / node_type / input_fields / output_fields
 * / node_config / children / __attributes__). For a StartNode the only
 * load-bearing fields are node_type=StartNode and an `output_fields`
 * entry the rest of the graph can reference.
 */
export async function seedStartNode(wfId: string, nodeId = 'node_1'): Promise<void> {
  // The `/edits` endpoint takes the JSON-Patch op shape (`[op, pointer, value]`)
  // the frontend now emits — the legacy `NODE_ADD` op was removed in the
  // JSON-Patch cutover. The pointer is the node id as a top-level JSON Pointer.
  await applyEdits(wfId, [
    [
      'add',
      `/${nodeId}`,
      {
        node_id: nodeId,
        node_name: '__start__',
        node_type: 'StartNode',
        node_description: '',
        input_fields: {},
        output_fields: {
          user_query: { type: 'string', description: 'user input' },
        },
        node_config: {},
        children: [],
        __attributes__: { x: 0, y: 0 },
      },
    ],
  ]);
}

/** Create a chat session against a workflow. Returns `chat_id`. */
export async function createChat(wfId: string, name = 'Smoke chat'): Promise<string> {
  const res = await fetch(`${API_BASE}/api/v1/workflows/${wfId}/chats`, {
    method: 'POST',
    headers: await authHeaders(),
    body: JSON.stringify({ name }),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`createChat failed: ${res.status} ${body}`);
  }
  const json = (await res.json()) as { chat_id: string };
  return json.chat_id;
}

/**
 * Seed Secure Cookies and an initial i18n locale before the page loads. The
 * HttpOnly Session credential is never exposed to JavaScript; the locale is
 * only initialized when absent so a tested language change survives subsequent
 * full-document navigations.
 *
 * Call from each spec's `test.beforeEach` instead of duplicating the
 * `addInitScript` block.
 */
export async function seedAuthAndLocale(
  context: import('@playwright/test').BrowserContext,
  locale = 'en',
): Promise<void> {
  const material = await getAuthMaterial();
  if (material.cookies.length) {
    await context.addCookies(material.cookies.map((cookie) => ({
      ...cookie,
      url: WEB_ORIGIN,
    })));
  }
  await seedLocale(context, locale);
}

/**
 * Seed an explicitly-owned Secure Cookie jar, used by tests that revoke their
 * Session. The historical function name remains to avoid churn in callers;
 * `token` is an opaque in-process handle and is never sent or persisted.
 */
export async function seedTokenAndLocale(
  context: import('@playwright/test').BrowserContext,
  token: string,
  locale = 'en',
): Promise<void> {
  const material = materialByHandle.get(token);
  if (!material) throw new Error('unknown E2E Session handle');
  await context.addCookies(material.cookies.map((cookie) => ({
    ...cookie,
    url: WEB_ORIGIN,
  })));
  await seedLocale(context, locale);
}

async function seedLocale(
  context: import('@playwright/test').BrowserContext,
  locale: string,
): Promise<void> {
  await context.addInitScript(
    (lng) => {
      // addInitScript also runs in sandboxed srcdoc frames. Those frames
      // intentionally omit allow-same-origin, so Web Storage access throws.
      try {
        if (window.localStorage.getItem('vibecanvas.locale') === null) {
          window.localStorage.setItem('vibecanvas.locale', lng);
        }
      } catch {
        // The top-level application origin is seeded; isolated render frames
        // must neither receive nor be able to read browser storage.
      }
    },
    locale,
  );
}
