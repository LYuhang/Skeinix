/**
 * MCP SP1-T7 — `McpServersPage` smoke tests.
 *
 * Spies on the REAL API client module (`@/lib/api/mcp-servers`) so the REAL
 * react-query hooks run end-to-end (mirrors `queries/__tests__/mcp-servers`).
 * We use `vi.spyOn` rather than `vi.mock` because under `isolate: false`
 * (shared module graph) a `vi.mock` of `@/lib/api/mcp-servers` is order-fragile
 * — another file that imports the REAL module shifts module-eval ordering and
 * the replacement silently fails to apply (see feedback_vitest_isolate_false).
 * A spy never swaps module identity, so it survives any sibling load order.
 * Asserts:
 *   - one card per server from a mocked list (2 servers)
 *   - the search box narrows to the matching server by name
 *   - Delete (through the row menu + confirm) calls the delete mutation
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';

import * as mcpClient from '@/lib/api/mcp-servers';

const SERVERS = [
  {
    id: 'mcp-1',
    name: 'Github tools',
    tool_prefix: 'gh',
    transport: 'sse',
    endpoint: 'https://github.example.com/sse',
    auth_mode: 'configuration',
    connection_status: 'not_required',
    auth_config: { type: 'bearer', token: '***' },
    enabled: true,
    last_handshake_status: 'ok',
    last_tool_count: 7,
    last_tool_names: [{ name: 'gh_search', description: 'search' }],
    last_handshake_at: '2026-06-01T00:00:00Z',
    access: {
      capabilities: ['view', 'use', 'update', 'manage_secret', 'delete', 'manage_access'],
      effective_role: 'manager',
      source: 'computed',
    },
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-02T00:00:00Z',
  },
  {
    id: 'mcp-2',
    name: 'Weather API',
    tool_prefix: 'wx',
    transport: 'streamable_http',
    endpoint: 'https://weather.example.com/mcp',
    auth_mode: 'none',
    connection_status: 'not_required',
    auth_config: { type: 'none' },
    enabled: false,
    last_handshake_status: 'error: timeout',
    last_tool_count: null,
    last_tool_names: null,
    last_handshake_at: null,
    access: {
      capabilities: ['view', 'use', 'update', 'manage_secret', 'delete', 'manage_access'],
      effective_role: 'manager',
      source: 'computed',
    },
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-02T00:00:00Z',
  },
];

const CATALOG = Array.from({ length: 20 }, (_, index) => ({
  source: 'official',
  source_id: `io.example/server-${index + 1}`,
  name: `Server ${index + 1}`,
  description: `Catalog server ${index + 1}`,
  version: '1.0.0',
  verified: true,
  usage_count: null,
  homepage: null,
  published_at: null,
  connection: null,
  config_fields: [],
  configuration_source: 'official_registry',
  auth_mode: 'none',
}));

const PLATFORM_SERVICES = [
  ['config', 'Configuration', 'Always available'],
  ['interactive', 'Interactive', 'Always available'],
  ['workflow', 'Workflow', 'Always available'],
  ['task', 'Task', '/task'],
  ['deployment', 'Deployment', '/deployment'],
  ['knowledge', 'Knowledge', '/knowledge'],
  ['build', 'Build & Run', '/build'],
  ['browser', 'Browser', '/browser'],
  ['plan', 'Execution Plan', '/plan'],
].map(([id, name, activation]) => ({
  id,
  name,
  description: `${name} platform tools`,
  activation,
  activation_mode: activation.startsWith('/') ? 'command' : 'base',
  runtime_types: id === 'plan' ? ['langchain'] : ['langchain', 'codex'],
  tools: [{
    name: `${id}_tool`,
    description: `${name} tool`,
    input_schema: { type: 'object', properties: {}, additionalProperties: false },
    annotations: {},
  }],
}));

// sonner toast is a side effect we don't assert on here. Spy (not vi.mock) for
// the same isolate=false order-stability reason as the API client above.
import * as sonner from 'sonner';

import { McpServersPage } from '@/pages/mcp-servers/McpServersPage';

void i18n.use(initReactI18next).init({
  lng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderPage(initialEntry = '/') {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <McpServersPage />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

describe('McpServersPage', () => {
  let deleteSpy: ReturnType<typeof vi.spyOn>;
  let catalogSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(mcpClient, 'listMcpServers').mockResolvedValue(SERVERS as never);
    vi.spyOn(mcpClient, 'listPlatformMcpServices').mockResolvedValue(PLATFORM_SERVICES as never);
    catalogSpy = vi.spyOn(mcpClient, 'searchMcpCatalog').mockImplementation(async (source, search, limit) => ({
      source,
      ranking: search ? 'search' : 'browse',
      items: CATALOG.slice(0, limit ?? CATALOG.length),
      has_more: (limit ?? CATALOG.length) < CATALOG.length,
    }) as never);
    deleteSpy = vi.spyOn(mcpClient, 'deleteMcpServer').mockResolvedValue(undefined as never);
    // Swallow toast side effects without asserting on them.
    vi.spyOn(sonner.toast, 'success').mockImplementation(() => '' as never);
    vi.spyOn(sonner.toast, 'error').mockImplementation(() => '' as never);
  });

  it('renders a card per server', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Github tools')).toBeInTheDocument(),
    );
    expect(screen.getByText('Weather API')).toBeInTheDocument();
    expect(screen.getAllByTestId('mcp-card')).toHaveLength(2);
  });

  it('labels row actions with the server name and current toggle action', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('Github tools')).toBeInTheDocument());

    expect(screen.getByRole('switch', { name: 'Disable' })).toBeChecked();
    expect(screen.getByRole('switch', { name: 'Enable' })).not.toBeChecked();
    expect(screen.getByRole('button', { name: 'Open actions for Github tools' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open actions for Weather API' })).toBeInTheDocument();
  });

  it('normalizes a stored no-auth configuration in the edit form', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByText('Weather API')).toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'Open actions for Weather API' }));
    await user.click(await screen.findByRole('menuitem', { name: 'Edit' }));

    expect(await screen.findByTestId('mcp-auth')).toHaveTextContent('None');
    expect(screen.queryByLabelText('Token')).not.toBeInTheDocument();
  });

  it('renders every live platform service as a detail link', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId('platform-mcp-task')).toBeInTheDocument());

    expect(screen.getByTestId('platform-mcp-deployment')).toHaveAttribute(
      'href',
      '/mcp-servers/platform/deployment',
    );
    expect(screen.getByTestId('platform-mcp-knowledge')).toBeInTheDocument();
    expect(screen.getAllByTestId(/^platform-mcp-/)).toHaveLength(9);
  });

  it('narrows by name when typing in the search box', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Github tools')).toBeInTheDocument(),
    );

    await user.type(screen.getByTestId('mcp-search'), 'weather');

    await waitFor(() =>
      expect(screen.queryByText('Github tools')).not.toBeInTheDocument(),
    );
    expect(screen.getByText('Weather API')).toBeInTheDocument();
    expect(screen.getAllByTestId('mcp-card')).toHaveLength(1);
  });

  it('deletes a server through its menu + confirm', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Github tools')).toBeInTheDocument(),
    );

    const card = screen.getAllByTestId('mcp-card')[0];
    // Open the ⋯ menu on the first card.
    await user.click(within(card).getByTestId('mcp-card-menu'));
    // Click "Delete" in the menu.
    await user.click(await screen.findByTestId('mcp-menu-delete'));
    // Confirm in the dialog.
    await user.click(await screen.findByTestId('mcp-confirm-delete'));

    await waitFor(() =>
      expect(deleteSpy).toHaveBeenCalledWith('mcp-1'),
    );
  });

  it('shows ten catalog entries initially and appends ten more on demand', async () => {
    const user = userEvent.setup();
    renderPage('/mcp-servers?tab=discover');

    await waitFor(() => expect(screen.getAllByTestId('mcp-catalog-card')).toHaveLength(10));
    await user.click(screen.getByTestId('mcp-catalog-more'));

    await waitFor(() => expect(screen.getAllByTestId('mcp-catalog-card')).toHaveLength(20));
  });

  it('searches only on Search or Enter and keeps install guidance above results', async () => {
    const user = userEvent.setup();
    renderPage('/mcp-servers?tab=discover');

    await waitFor(() => expect(screen.getAllByTestId('mcp-catalog-card')).toHaveLength(10));
    expect(catalogSpy).toHaveBeenCalledTimes(1);

    const input = screen.getByTestId('mcp-catalog-search');
    await user.type(input, 'github');
    expect(catalogSpy).toHaveBeenCalledTimes(1);

    await user.click(screen.getByTestId('mcp-catalog-search-button'));
    await waitFor(() => expect(catalogSpy).toHaveBeenLastCalledWith('official', 'github', 10));
    expect(screen.queryByText('Search Results')).not.toBeInTheDocument();

    const guideline = screen.getByText('Install Guideline');
    const firstCard = screen.getAllByTestId('mcp-catalog-card')[0];
    expect(guideline.compareDocumentPosition(firstCard) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.clear(input);
    await user.type(input, 'filesystem{Enter}');
    await waitFor(() => expect(catalogSpy).toHaveBeenLastCalledWith('official', 'filesystem', 10));
  });

  it('keeps Search available while the initial catalog request is still loading', async () => {
    catalogSpy.mockImplementation(() => new Promise(() => {}) as never);
    const user = userEvent.setup();
    renderPage('/mcp-servers?tab=discover');

    const button = await screen.findByTestId('mcp-catalog-search-button');
    expect(button).toBeEnabled();
    await user.type(screen.getByTestId('mcp-catalog-search'), 'github');
    await user.click(button);

    await waitFor(() => expect(catalogSpy).toHaveBeenLastCalledWith('official', 'github', 10));
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent('Searching');
  });
});
