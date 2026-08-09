import { resolveApiUrl } from '@/lib/base-path';
import { sessionFetch } from '@/lib/api/session-fetch';
import { useAuthStore } from '@/stores/auth';

export interface EnterpriseIdentityProvider {
  provider_id: string;
  organization_id: string;
  display_name: string;
  issuer_url: string;
  client_id: string;
  token_endpoint_auth_method: 'client_secret_basic' | 'client_secret_post' | 'none';
  has_client_secret: boolean;
  subject_claim: string;
  email_claim: string;
  display_name_claim: string;
  scopes: string[];
  status: 'active' | 'disabled';
  scim_token_generation: number;
  scim_token_expires_at: string | null;
  scim_base_url: string | null;
  oidc_callback_url: string | null;
  last_scim_sync_at: string | null;
  created_at: string;
  updated_at: string;
  scim_token?: string;
}

export interface CreateEnterpriseIdentityProviderInput {
  display_name: string;
  issuer_url: string;
  client_id: string;
  client_secret?: string;
  token_endpoint_auth_method?: 'client_secret_basic' | 'client_secret_post' | 'none';
  scim_token_ttl_days?: number;
}

export interface EnterpriseSsoProvider {
  provider_id: string;
  display_name: string;
}

export class EnterpriseIdentityApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await sessionFetch(resolveApiUrl(path), {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  if (response.status === 401) useAuthStore.getState().handle401();
  const payload = await response.json().catch(() => null) as
    | { detail?: unknown }
    | null;
  if (!response.ok) {
    const detail = payload?.detail;
    throw new EnterpriseIdentityApiError(
      response.status,
      typeof detail === 'string'
        ? detail
        : `enterprise_identity_request_failed_${response.status}`,
    );
  }
  return payload as T;
}

function providerPath(organizationId: string): string {
  return `/api/v1/organizations/${encodeURIComponent(organizationId)}/identity-providers`;
}

export async function listEnterpriseIdentityProviders(
  organizationId: string,
): Promise<EnterpriseIdentityProvider[]> {
  const response = await requestJson<{ items: EnterpriseIdentityProvider[] }>(
    providerPath(organizationId),
  );
  return response.items;
}

export function createEnterpriseIdentityProvider(
  organizationId: string,
  input: CreateEnterpriseIdentityProviderInput,
): Promise<EnterpriseIdentityProvider> {
  return requestJson(providerPath(organizationId), {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function setEnterpriseIdentityProviderStatus(
  organizationId: string,
  providerId: string,
  status: 'active' | 'disabled',
): Promise<EnterpriseIdentityProvider> {
  return requestJson(
    `${providerPath(organizationId)}/${encodeURIComponent(providerId)}`,
    { method: 'PATCH', body: JSON.stringify({ status }) },
  );
}

export function updateEnterpriseOidcClientAuth(
  organizationId: string,
  providerId: string,
  tokenEndpointAuthMethod: 'client_secret_basic' | 'client_secret_post' | 'none',
  clientSecret?: string,
): Promise<EnterpriseIdentityProvider> {
  return requestJson(
    `${providerPath(organizationId)}/${encodeURIComponent(providerId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify({
        token_endpoint_auth_method: tokenEndpointAuthMethod,
        ...(clientSecret ? { client_secret: clientSecret } : {}),
      }),
    },
  );
}

export function rotateEnterpriseScimToken(
  organizationId: string,
  providerId: string,
  ttlDays = 365,
): Promise<EnterpriseIdentityProvider> {
  return requestJson(
    `${providerPath(organizationId)}/${encodeURIComponent(providerId)}/scim-token`,
    { method: 'POST', body: JSON.stringify({ ttl_days: ttlDays }) },
  );
}

export async function discoverOrganizationSso(
  organizationSlug: string,
): Promise<EnterpriseSsoProvider[]> {
  const slug = organizationSlug.trim().toLocaleLowerCase('en-US');
  const response = await fetch(resolveApiUrl(
    `/api/v1/auth/sso/organizations/${encodeURIComponent(slug)}/providers`,
  ), {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  });
  const payload = await response.json().catch(() => null) as
    | { items?: unknown }
    | null;
  if (!response.ok) {
    throw new EnterpriseIdentityApiError(
      response.status,
      `enterprise_sso_discovery_failed_${response.status}`,
    );
  }
  if (!Array.isArray(payload?.items)) return [];
  return payload.items.flatMap((item): EnterpriseSsoProvider[] => {
    if (!item || typeof item !== 'object') return [];
    const provider = item as Record<string, unknown>;
    return typeof provider.provider_id === 'string'
      && typeof provider.display_name === 'string'
      ? [{
          provider_id: provider.provider_id,
          display_name: provider.display_name,
        }]
      : [];
  });
}

export function enterpriseSsoStartUrl(
  providerId: string,
  returnTo = '/chat',
): string {
  const query = new URLSearchParams({ return_to: returnTo });
  return resolveApiUrl(
    `/api/v1/auth/sso/providers/${encodeURIComponent(providerId)}/start?${query}`,
  );
}
