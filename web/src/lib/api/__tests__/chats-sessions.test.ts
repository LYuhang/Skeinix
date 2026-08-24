import { afterEach, describe, expect, it, vi } from 'vitest';

import { fetchAllChatSessions } from '@/lib/api/queries/chats';

vi.mock('@/stores/auth', () => ({
  useAuthStore: { getState: () => ({ token: 'test-token', handle401: () => {} }) },
}));

afterEach(() => vi.restoreAllMocks());

describe('fetchAllChatSessions', () => {
  it('continues through every server page instead of hiding older chats', async () => {
    const firstItems = Array.from({ length: 500 }, (_, index) => ({
      chat_id: `chat-${index}`,
    }));
    const fetchSpy = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: firstItems,
        total: 501,
        limit: 500,
        offset: 0,
      }), { status: 200, headers: { 'content-type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        items: [{ chat_id: 'chat-500' }],
        total: 501,
        limit: 500,
        offset: 500,
      }), { status: 200, headers: { 'content-type': 'application/json' } }));

    const result = await fetchAllChatSessions('scope with spaces', 'chat');

    expect(result.items).toHaveLength(501);
    expect(result.items.at(-1)).toMatchObject({ chat_id: 'chat-500' });
    expect(result.total).toBe(501);
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    expect(String(fetchSpy.mock.calls[0]?.[0])).toContain(
      '/chat-scopes/scope%20with%20spaces/chats?surface=chat&limit=500&offset=0',
    );
    expect(String(fetchSpy.mock.calls[1]?.[0])).toContain('limit=500&offset=500');
  });
});
