import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { sessionFetch } from '@/lib/api/session-fetch';
import {
  STEP_UP_REQUEST_EVENT,
  type StepUpRequestDetail,
} from '@/lib/auth/step-up-broker';

describe('sessionFetch', () => {
  beforeEach(() => {
    document.cookie = 'vibecanvas-web-csrf=csrf_value; Path=/';
  });

  afterEach(() => {
    document.cookie = 'vibecanvas-support-csrf=; Max-Age=0; Path=/';
    document.cookie = 'vibecanvas-web-csrf=; Max-Age=0; Path=/';
    vi.unstubAllGlobals();
  });

  it('uses the support CSRF namespace while privileged mode is active', async () => {
    document.cookie = 'vibecanvas-support-csrf=support_csrf; Path=/';
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    await sessionFetch('http://localhost/api/v1/auth/privileged-access/exit', {
      method: 'POST',
    });

    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.headers.get('X-CSRF-Token')).toBe('support_csrf');
  });

  it('adds ambient credentials and CSRF only to unsafe platform requests', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    await sessionFetch('http://localhost/api/v1/workflows', {
      method: 'POST',
      body: '{}',
    });

    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.credentials).toBe('include');
    expect(request.headers.get('X-CSRF-Token')).toBe('csrf_value');
    expect(request.headers.has('Authorization')).toBe(false);
  });

  it('does not leak credentials or CSRF metadata to third-party URLs', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    await sessionFetch('https://example.test/upload', { method: 'POST' });

    const request = fetchMock.mock.calls[0]?.[0] as Request;
    expect(request.credentials).toBe('same-origin');
    expect(request.headers.has('X-CSRF-Token')).toBe(false);
    expect(request.headers.has('X-VibeCanvas-Session-Audience')).toBe(false);
  });

  it('performs one claimed WebAuthn step-up and retries with refreshed CSRF', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        detail: { code: 'step_up_required', method: 'webauthn' },
      }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);
    const listener = (rawEvent: Event) => {
      const event = rawEvent as CustomEvent<StepUpRequestDetail>;
      event.preventDefault();
      document.cookie = 'vibecanvas-web-csrf=rotated_csrf; Path=/';
      event.detail.complete(true);
    };
    window.addEventListener(STEP_UP_REQUEST_EVENT, listener);

    try {
      const response = await sessionFetch('http://localhost/api/v1/workflows/wf/access', {
        method: 'POST',
        body: '{"relation":"viewer"}',
      });
      expect(response.status).toBe(204);
      expect(fetchMock).toHaveBeenCalledTimes(2);
      const first = fetchMock.mock.calls[0]?.[0] as Request;
      const retried = fetchMock.mock.calls[1]?.[0] as Request;
      expect(first.headers.get('X-CSRF-Token')).toBe('csrf_value');
      expect(retried.headers.get('X-CSRF-Token')).toBe('rotated_csrf');
      expect(await retried.text()).toBe('{"relation":"viewer"}');
    } finally {
      window.removeEventListener(STEP_UP_REQUEST_EVENT, listener);
    }
  });

  it('does not hang or retry when no step-up UI claims the request', async () => {
    const denied = new Response(JSON.stringify({
      detail: { code: 'step_up_required', method: 'webauthn' },
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    });
    const fetchMock = vi.fn().mockResolvedValue(denied);
    vi.stubGlobal('fetch', fetchMock);

    const response = await sessionFetch('http://localhost/api/v1/organizations/org/members/user', {
      method: 'PATCH',
      body: '{}',
    });
    expect(response.status).toBe(403);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
