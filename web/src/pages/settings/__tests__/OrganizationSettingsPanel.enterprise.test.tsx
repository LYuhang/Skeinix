import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import i18n from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { OrganizationSettingsPanel } from '@/pages/settings/OrganizationSettingsPanel';
import { useAuthStore } from '@/stores/auth';

const mocks = vi.hoisted(() => ({
  listProviders: vi.fn(),
}));

vi.mock('@/lib/api/organizations', () => ({
  listOrganizations: vi.fn(async () => ({
    active_organization_id: 'organization-1',
    session_generation: 1,
    items: [{
      organization_id: 'organization-1',
      kind: 'business',
      slug: 'enterprise',
      name: 'Enterprise Workspace',
      membership_id: 'membership-owner',
      role: 'owner',
      status: 'active',
      active: true,
      access: {
        capabilities: ['view_audit', 'manage_members', 'manage_policy'],
        effective_role: 'owner',
        source: 'direct',
      },
    }],
  })),
  listOrganizationMembers: vi.fn(async () => [{
    membership_id: 'membership-scim',
    user_id: 'directory-user',
    email: 'directory@example.com',
    display_name: 'Directory User',
    role: 'member',
    status: 'active',
    source: 'scim',
    directory_provider_id: 'provider-1',
    created_at: '2026-08-02T00:00:00Z',
    updated_at: '2026-08-02T00:00:00Z',
  }]),
  getOrganizationSelf: vi.fn(async () => ({
    membership: {
      membership_id: 'membership-owner',
      user_id: 'owner-user',
      email: 'owner@example.com',
      display_name: 'Owner User',
      role: 'owner',
      status: 'active',
      source: 'native',
      directory_provider_id: null,
      created_at: '2026-08-02T00:00:00Z',
      updated_at: '2026-08-02T00:00:00Z',
    },
    groups: [],
  })),
  listOrganizationGroups: vi.fn(async () => [{
    group_id: 'group-idp',
    organization_id: 'organization-1',
    parent_group_id: null,
    kind: 'team',
    name: 'Directory Engineering',
    source: 'idp',
    directory_provider_id: 'provider-1',
    external_id: 'directory-group',
    status: 'active',
    created_by: 'owner-user',
    created_at: '2026-08-02T00:00:00Z',
    updated_at: '2026-08-02T00:00:00Z',
    access: {
      capabilities: ['view_metadata', 'manage_members'],
      effective_role: 'manager',
      source: 'direct',
    },
  }]),
  listGroupMembers: vi.fn(async () => [{
    membership_id: 'group-membership',
    user_id: 'directory-user',
    email: 'directory@example.com',
    display_name: 'Directory User',
    role: 'member',
    status: 'active',
    created_at: '2026-08-02T00:00:00Z',
    updated_at: '2026-08-02T00:00:00Z',
  }]),
  listServiceAccounts: vi.fn(async () => []),
  createOrganization: vi.fn(),
  createOrganizationGroup: vi.fn(),
  removeGroupMember: vi.fn(),
  rotateServiceAccountGeneration: vi.fn(),
  setGroupMember: vi.fn(),
  updateOrganizationMember: vi.fn(),
  updateServiceAccountStatus: vi.fn(),
}));

vi.mock('@/lib/api/enterprise-identity', () => ({
  listEnterpriseIdentityProviders: mocks.listProviders,
  createEnterpriseIdentityProvider: vi.fn(),
  rotateEnterpriseScimToken: vi.fn(),
  setEnterpriseIdentityProviderStatus: vi.fn(),
  updateEnterpriseOidcClientAuth: vi.fn(),
}));

vi.mock('@/lib/api/audit', () => ({
  listAudit: vi.fn(async () => ({ items: [], next_cursor: null })),
}));

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={testI18n}>
        <OrganizationSettingsPanel />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe('enterprise organization identity controls', () => {
  beforeEach(() => {
    useAuthStore.setState({
      authenticated: true,
      bootstrapped: true,
      user: {
        user_id: 'owner-user',
        tenant_id: 'organization-1',
        email: 'owner@example.com',
        displayName: 'Owner User',
      },
    });
    mocks.listProviders.mockResolvedValue([{
      provider_id: 'provider-1',
      organization_id: 'organization-1',
      display_name: 'Corporate Identity',
      issuer_url: 'https://login.example.com',
      client_id: 'vibecanvas',
      token_endpoint_auth_method: 'client_secret_basic',
      has_client_secret: true,
      subject_claim: 'sub',
      email_claim: 'email',
      display_name_claim: 'name',
      scopes: ['openid', 'profile', 'email'],
      status: 'active',
      scim_token_generation: 1,
      scim_token_expires_at: '2027-08-02T00:00:00Z',
      scim_base_url: 'https://app.example.com/scim/v2/provider-1',
      oidc_callback_url: 'https://app.example.com/api/v1/auth/sso/callback',
      last_scim_sync_at: null,
      created_at: '2026-08-02T00:00:00Z',
      updated_at: '2026-08-02T00:00:00Z',
    }]);
  });

  it('shows the unified OIDC/SCIM control and keeps directory rows read-only', async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(await screen.findByRole('tab', { name: /people/i }));
    // Directory-owned rows are rendered as honest read-only badges, not as
    // disabled form controls that suggest the value could be changed here.
    expect(screen.queryAllByRole('combobox')).toHaveLength(0);
    expect(screen.getByText('SCIM')).toBeInTheDocument();

    await user.click(screen.getByRole('tab', { name: /departments & teams/i }));
    expect(screen.getByText('IdP')).toBeInTheDocument();
    expect(await screen.findByText(/managed by the identity provider/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^add member$/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /remove member/i })).toBeNull();

    await user.click(screen.getByRole('tab', { name: /security & identity/i }));
    expect(await screen.findByTestId('enterprise-identity-section')).toBeInTheDocument();
    expect(await screen.findByText('Corporate Identity')).toBeInTheDocument();
    expect(screen.getByText('OIDC + SCIM')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /oidc client auth/i })).toBeInTheDocument();
    expect(screen.queryByText(/private chat inventory/i)).toBeNull();
    expect(screen.queryByText(/^audit log$/i)).toBeNull();
    expect(mocks.listProviders).toHaveBeenCalledTimes(1);
  });
});
