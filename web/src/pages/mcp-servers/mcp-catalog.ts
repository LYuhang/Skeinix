import type { McpCatalogItem, McpServer, McpServerInput } from '@/lib/api/mcp-servers';

function replaceUrlVariable(url: string, name: string, value: string): string {
  return url.replaceAll(`{${name}}`, encodeURIComponent(value));
}

function addQueryValue(url: string, name: string, value: string): string {
  const parsed = new URL(url);
  parsed.searchParams.set(name, value);
  return parsed.toString();
}

function basePrefix(item: McpCatalogItem): string {
  const slug = (item.source_id.split('/').pop() || item.name)
    .toLowerCase()
    .replace(/mcp[-_]?server/g, '')
    .replace(/[-_]?mcp/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 24);
  return /^[a-z]/.test(slug) ? slug || 'mcp' : `mcp_${slug || 'server'}`;
}

export function availablePrefix(item: McpCatalogItem, installed: McpServer[]): string {
  const base = basePrefix(item).slice(0, 31);
  const used = new Set(installed.map((server) => server.tool_prefix));
  if (!used.has(base)) return base;
  for (let suffix = 2; suffix < 1000; suffix += 1) {
    const tail = `_${suffix}`;
    const candidate = `${base.slice(0, 31 - tail.length)}${tail}`;
    if (!used.has(candidate)) return candidate;
  }
  return `mcp_${Date.now().toString(36)}`.slice(0, 31);
}

export function buildCatalogInstallInput(
  item: McpCatalogItem,
  installed: McpServer[],
  values: Record<string, string>,
): McpServerInput {
  if (!item.connection) throw new Error('This catalog entry has no supported connection.');
  const connectionConfig = structuredClone(item.connection.connection_config);
  const headers = { ...((connectionConfig.headers as Record<string, string> | undefined) ?? {}) };
  const env = { ...((connectionConfig.env as Record<string, string> | undefined) ?? {}) };
  let endpoint = item.connection.endpoint;
  let connectionUrl = endpoint;
  let auth: McpServerInput['auth_config'] = { type: 'none' };

  for (const field of item.config_fields) {
    const value = values[field.key]?.trim() ?? '';
    if (!value) continue;
    if (field.target === 'bearer') auth = { type: 'bearer', token: value };
    else if (field.target.startsWith('header:')) headers[field.target.slice(7)] = value;
    else if (field.target.startsWith('env:')) env[field.target.slice(4)] = value;
    else if (field.target.startsWith('query:')) {
      connectionUrl = addQueryValue(connectionUrl, field.target.slice(6), value);
    }
    else if (field.target.startsWith('url_variable:')) {
      endpoint = replaceUrlVariable(endpoint, field.target.slice(13), value);
      connectionUrl = replaceUrlVariable(connectionUrl, field.target.slice(13), value);
    }
  }
  if (item.connection.transport !== 'stdio') connectionConfig.url = connectionUrl;
  if (Object.keys(headers).length > 0) connectionConfig.headers = headers;
  if (Object.keys(env).length > 0) connectionConfig.env = env;

  return {
    name: item.name.slice(0, 200),
    tool_prefix: availablePrefix(item, installed),
    transport: item.connection.transport,
    endpoint,
    description: item.description || null,
    connection_config: connectionConfig,
    auth_config: auth,
  };
}
