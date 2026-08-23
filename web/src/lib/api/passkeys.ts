import { getApiBase } from '@/lib/base-path';
import { sessionFetch } from '@/lib/api/session-fetch';
import { useAuthStore } from '@/stores/auth';

const BASE = getApiBase();

export interface WebAuthnCredentialSummary {
  credential_id: string;
  name: string;
  device_type: string;
  backed_up: boolean;
  transports: string[];
  created_at: string;
  last_used_at: string | null;
}

export interface WebAuthnStatus {
  enabled: boolean;
  credentials: WebAuthnCredentialSummary[];
  authentication_strength: string;
  step_up_expires_at: string | null;
}

export class PasskeyApiError extends Error {
  readonly status: number;
  readonly detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : `Passkey request failed (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set('Accept', 'application/json');
  if (init.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  const response = await sessionFetch(`${BASE}${path}`, {
    ...init,
    headers,
    credentials: 'include',
  });
  if (response.status === 401) useAuthStore.getState().handle401();
  if (response.status === 204 || response.status === 205) return undefined as T;
  const payload = await response.json().catch(() => null) as
    | { detail?: unknown }
    | null;
  if (!response.ok) {
    throw new PasskeyApiError(
      response.status,
      payload?.detail ?? `HTTP ${response.status}`,
    );
  }
  return payload as T;
}

export const getWebAuthnStatus = () =>
  request<WebAuthnStatus>('/api/v1/auth/passkeys');

export const beginWebAuthnRegistration = (password: string) =>
  request<PublicKeyCredentialCreationOptionsJSON>(
    '/api/v1/auth/passkeys/registration/options',
    { method: 'POST', body: JSON.stringify({ password }) },
  );

export const finishWebAuthnRegistration = (
  credential: RegistrationResponseJSON,
  name: string,
) => request<{ authentication_strength: 'webauthn'; step_up_expires_at: string }>(
  '/api/v1/auth/passkeys/registration/verify',
  { method: 'POST', body: JSON.stringify({ credential, name }) },
);

export const beginWebAuthnAuthentication = () =>
  request<PublicKeyCredentialRequestOptionsJSON>(
    '/api/v1/auth/passkeys/authentication/options',
    { method: 'POST' },
  );

export const finishWebAuthnAuthentication = (
  credential: AuthenticationResponseJSON,
) => request<{ authentication_strength: 'webauthn'; step_up_expires_at: string }>(
  '/api/v1/auth/passkeys/authentication/verify',
  { method: 'POST', body: JSON.stringify({ credential }) },
);

export const deleteWebAuthnCredential = (
  credentialId: string,
  password: string,
) => request<void>(
  `/api/v1/auth/passkeys/credentials/${encodeURIComponent(credentialId)}`,
  { method: 'DELETE', body: JSON.stringify({ password }) },
);

// TypeScript's DOM declarations still omit the JSON transport types in some
// supported compiler/browser combinations. Keep the wire shapes local.
export type PublicKeyCredentialCreationOptionsJSON = Record<string, unknown>;
export type PublicKeyCredentialRequestOptionsJSON = Record<string, unknown>;
export type RegistrationResponseJSON = Record<string, unknown>;
export type AuthenticationResponseJSON = Record<string, unknown>;
