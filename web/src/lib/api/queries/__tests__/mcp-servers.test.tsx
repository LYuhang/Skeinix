import { renderHook, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useMcpServers } from '@/lib/api/queries/mcp-servers';
import * as client from '@/lib/api/mcp-servers';
import type { ReactNode } from 'react';

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe('useMcpServers', () => {
  beforeEach(() => vi.restoreAllMocks());
  it('returns the listed servers', async () => {
    vi.spyOn(client, 'listMcpServers').mockResolvedValue([
      { id: 's1', name: 'A', tool_prefix: 'a', transport: 'sse', endpoint: 'http://x', auth_mode: 'none', connection_status: 'not_required', auth_config: null, enabled: true, last_handshake_status: 'ok', last_tool_count: 2, last_tool_names: null, last_handshake_at: null, created_at: '', updated_at: '', provenance: { ownership_scope: 'personal', origin_type: 'created', owner: { type: 'user', display_name: 'Owner' }, created_by: { type: 'user', display_name: 'Owner' } } },
    ]);
    const { result } = renderHook(() => useMcpServers(), { wrapper });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data?.[0].name).toBe('A');
  });
});
