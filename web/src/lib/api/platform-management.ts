import { resolveApiUrl } from '@/lib/base-path';
import { sessionFetch } from '@/lib/api/session-fetch';

export type PlatformManagementRole = 'platform_support' | 'platform_security_admin';

export interface PlatformManagementContext {
  role: PlatformManagementRole;
}

export interface PlatformManagementOverview extends PlatformManagementContext {
  generated_at: string;
  identity: {
    registered_users: number;
    active_users: number;
    online_users_5m: number;
    registered_users_24h: number;
    personal_workspaces: number;
    company_workspaces: number;
  };
  organizations: Array<{
    organization_id: string;
    name: string;
    member_count: number;
    active_member_count: number;
  }>;
  host: {
    cpu_count: number;
    load_average_1m: number;
    load_average_5m: number;
    load_average_15m: number;
    memory: { total_bytes: number | null; available_bytes: number | null };
    disk: { total_bytes: number; free_bytes: number };
    scope: string;
  };
  sandboxes: {
    resident: number;
    capacity: number;
    busy: number;
    resident_leases: number;
    pending_closes: number;
  };
  privacy: {
    content_visible: false;
    user_profiles_visible: false;
    scope: string;
  };
}

export type PlatformAuditCategory =
  | 'identity'
  | 'access_security'
  | 'resources'
  | 'data_lifecycle'
  | 'runtime_operations';

export interface PlatformAuditReport extends PlatformManagementContext {
  generated_at: string;
  window_hours: number;
  bucket: 'hour' | 'day';
  categories: Array<{
    category: PlatformAuditCategory;
    total: number;
    failures: number;
    series: Array<{ ts: string; total: number; failures: number }>;
    actions: Array<{ action: string; total: number; failures: number }>;
  }>;
  recent_events: Array<{
    event_id: string;
    category: PlatformAuditCategory;
    action: string;
    target_type: string | null;
    outcome: 'success' | 'failure';
    created_at: string;
  }>;
  catalog: Array<{
    category: PlatformAuditCategory;
    actions: string[];
    missing_objects: string[];
    coverage: 'complete' | 'partial';
  }>;
  privacy: {
    content_visible: false;
    identities_visible: false;
    customer_resource_identifiers_visible: false;
    private_payload_decrypted: false;
  };
}

async function request<T>(path: string): Promise<T> {
  const response = await sessionFetch(resolveApiUrl(path), {
    credentials: 'include',
    headers: { Accept: 'application/json' },
  });
  if (!response.ok) throw new Error(`platform_management_${response.status}`);
  return response.json() as Promise<T>;
}

export const getPlatformManagementContext = () =>
  request<PlatformManagementContext>('/api/v1/platform-management/context');

export const getPlatformManagementOverview = () =>
  request<PlatformManagementOverview>('/api/v1/platform-management/overview');

export const getPlatformAuditReport = (windowHours: number) =>
  request<PlatformAuditReport>(`/api/v1/platform-management/audit?window_hours=${windowHours}`);
