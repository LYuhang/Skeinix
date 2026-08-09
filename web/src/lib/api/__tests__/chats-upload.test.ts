import { afterEach, describe, expect, it, vi } from 'vitest';
import { uploadChatAttachment } from '@/lib/api/queries/chats';

vi.mock('@/stores/auth', () => ({
  useAuthStore: { getState: () => ({ token: 'test-token', handle401: () => {} }) },
}));

afterEach(() => vi.restoreAllMocks());

describe('uploadChatAttachment', () => {
  it('posts the source File as FormData without overriding the content type', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(JSON.stringify({
        type: 'image',
        name: 'photo.png',
        path: '/data/attachments/photo.png',
        content_type: 'image/png',
        size_bytes: 3,
      }), {
        status: 201,
        headers: { 'content-type': 'application/json' },
      }),
    );
    const file = new File(['img'], 'photo.png', { type: 'image/png' });

    await uploadChatAttachment({
      scopeId: 'scope with spaces',
      chatId: 'chat/segment',
      file,
      type: 'image',
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain(
      '/api/v1/chat-scopes/scope%20with%20spaces/chats/chat%2Fsegment/attachments?attachment_type=image',
    );
    expect((init as RequestInit).method).toBe('POST');
    const body = (init as RequestInit).body;
    expect(body).toBeInstanceOf(FormData);
    const uploaded = (body as FormData).get('file');
    expect(uploaded).toBeInstanceOf(File);
    expect(uploaded).toMatchObject({
      name: 'photo.png',
      type: 'image/png',
      size: 3,
    });
    const headers = new Headers((init as RequestInit).headers);
    expect(headers.get('Content-Type')).toBeNull();
    expect(headers.get('Authorization')).toBe('Bearer test-token');
  });
});
