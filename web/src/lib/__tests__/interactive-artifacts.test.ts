import { describe, expect, it } from 'vitest';
import { resolveInteractiveResourceUrl } from '@/lib/api/interactive-artifacts';

const session = {
  resource_mounts: [
    {
      path_prefix: '/mount/',
      root_url: 'https://proxy.example.test/pws/session/tasks/api/v1/vfs/resources/mount-token/mount/',
    },
    {
      path_prefix: '/',
      root_url: 'https://proxy.example.test/pws/session/tasks/api/v1/vfs/resources/chat-token/',
    },
  ],
  base_url: 'https://proxy.example.test/pws/session/tasks/api/v1/vfs/resources/chat-token/data/',
};

describe('interactive artifact resource mounts', () => {
  it('strips the matched mount prefix instead of duplicating it', () => {
    expect(resolveInteractiveResourceUrl('/mount/data/frame.png', session)).toBe(
      'https://proxy.example.test/pws/session/tasks/api/v1/vfs/resources/mount-token/mount/data/frame.png',
    );
  });

  it('routes arbitrary absolute workspace paths through the root capability', () => {
    expect(resolveInteractiveResourceUrl('/exec/results/report.json', session)).toBe(
      'https://proxy.example.test/pws/session/tasks/api/v1/vfs/resources/chat-token/exec/results/report.json',
    );
  });

  it('keeps relative paths based at the artifact data directory', () => {
    expect(resolveInteractiveResourceUrl('images/frame.png', session)).toBe(
      'https://proxy.example.test/pws/session/tasks/api/v1/vfs/resources/chat-token/data/images/frame.png',
    );
  });
});
