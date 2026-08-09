import { afterEach, describe, expect, it, vi } from 'vitest';
import { saveCustomSkill } from '@/lib/api/skills';

vi.mock('@/stores/auth', () => ({
  useAuthStore: { getState: () => ({ token: 'test-token', handle401: () => {} }) },
}));

afterEach(() => vi.restoreAllMocks());

describe('saveCustomSkill', () => {
  it('sends the Skill archive as FormData without overriding its content type', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          id: 'skill-1',
          name: 'uploaded-skill',
          description: 'Uploaded Skill',
          allowed_tools: [],
          version: 1,
          created_at: null,
          updated_at: null,
        }),
        {
          status: 201,
          headers: { 'content-type': 'application/json' },
        },
      ),
    );
    const bundle = new File(['skill archive'], 'uploaded-skill.zip', {
      type: 'application/zip',
    });

    await saveCustomSkill({ bundle });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain('/api/v1/skills/custom');
    expect((init as RequestInit).method).toBe('POST');
    const body = (init as RequestInit).body;
    expect(body).toBeInstanceOf(FormData);
    expect((body as FormData).get('bundle')).toBe(bundle);
    const headers = new Headers((init as RequestInit).headers);
    expect(headers.get('Content-Type')).toBeNull();
    expect(headers.get('Authorization')).toBe('Bearer test-token');
  });
});
