import type { BrowserContext } from '@playwright/test';

export const E2E_API_BASE = process.env.VIBECANVAS_API_BASE ?? 'http://127.0.0.1:8000';
export const E2E_APP_ORIGIN = process.env.VIBECANVAS_E2E_ORIGIN
  ?? `http://${process.env.VIBECANVAS_E2E_HOST ?? '127.0.0.1'}:${process.env.VIBECANVAS_WEB_PORT ?? '5173'}`;

export class E2ECookieSession {
  private readonly cookies = new Map<string, string>();

  private remember(response: Response) {
    for (const value of response.headers.getSetCookie()) {
      const pair = value.split(';', 1)[0];
      const separator = pair.indexOf('=');
      if (separator <= 0) continue;
      this.cookies.set(pair.slice(0, separator), pair.slice(separator + 1));
    }
  }

  private cookieHeader() {
    return [...this.cookies.entries()]
      .map(([name, value]) => `${name}=${value}`)
      .join('; ');
  }

  private csrfToken() {
    return [...this.cookies.entries()].find(([name]) => (
      name.endsWith('vibecanvas-web-csrf')
    ))?.[1] ?? '';
  }

  async register(label: string) {
    const suffix = `${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
    const useTestUser = process.env.VIBECANVAS_E2E_USE_TEST_USER === '1';
    const response = await fetch(
      `${E2E_API_BASE}/api/v1/auth/${useTestUser ? 'login' : 'register'}`,
      {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Origin: E2E_APP_ORIGIN },
      body: JSON.stringify(useTestUser
        ? { email: 'test', password: 'test' }
        : {
            email: `${label}_${suffix}@example.com`,
            username: label,
            password: 'E2E-cookie-session-42!',
          }),
      },
    );
    this.remember(response);
    if (!response.ok) {
      throw new Error(
        `${useTestUser ? 'login' : 'register'} failed: ${response.status} ${await response.text()}`,
      );
    }
  }

  async api(path: string, init: RequestInit = {}, allowError = false) {
    const method = (init.method ?? 'GET').toUpperCase();
    const response = await fetch(`${E2E_API_BASE}${path}`, {
      ...init,
      headers: {
        'Content-Type': 'application/json',
        Cookie: this.cookieHeader(),
        Origin: E2E_APP_ORIGIN,
        ...(method === 'GET' || method === 'HEAD'
          ? {}
          : { 'X-CSRF-Token': this.csrfToken() }),
        ...init.headers,
      },
    });
    this.remember(response);
    if (!allowError && !response.ok) {
      throw new Error(`${method} ${path} failed: ${response.status} ${await response.text()}`);
    }
    return response;
  }

  async form(path: string, body: FormData, allowError = false) {
    const response = await fetch(`${E2E_API_BASE}${path}`, {
      method: 'POST',
      headers: {
        Cookie: this.cookieHeader(),
        Origin: E2E_APP_ORIGIN,
        'X-CSRF-Token': this.csrfToken(),
      },
      body,
    });
    this.remember(response);
    if (!allowError && !response.ok) {
      throw new Error(`POST ${path} failed: ${response.status} ${await response.text()}`);
    }
    return response;
  }

  async seed(context: BrowserContext, locale = 'en') {
    await context.addCookies([...this.cookies.entries()].map(([name, value]) => ({
      name,
      value,
      url: E2E_API_BASE,
    })));
    await context.addInitScript((language) => {
      try {
        window.localStorage.setItem('vibecanvas.locale', language);
      } catch {
        // Opaque Preview frames intentionally cannot access Web Storage.
      }
    }, locale);
  }
}
