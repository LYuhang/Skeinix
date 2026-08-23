import { beforeEach, describe, expect, it } from 'vitest';

import { readChatViewPreferences, writeChatViewPreferences, EMPTY_CHAT_VIEW_STATE } from '@/lib/chat/preview-state';
import { chatAccountNamespace, chatClientStateKey } from '@/lib/chat/state-key';

describe('chat client state identity', () => {
  beforeEach(() => window.localStorage.clear());

  it('namespaces the same chat by tenant, user, scope, and surface', () => {
    const accountA = { tenant_id: 'tenant-a', user_id: 'user-a' };
    const accountB = { tenant_id: 'tenant-b', user_id: 'user-a' };
    expect(chatAccountNamespace(accountA)).toBe('tenant-a:user-a');
    expect(chatClientStateKey({ account: accountA, scopeId: 'scope', surface: 'chat', chatId: 'chat-1' }))
      .not.toBe(chatClientStateKey({ account: accountB, scopeId: 'scope', surface: 'chat', chatId: 'chat-1' }));
    expect(chatClientStateKey({ account: accountA, scopeId: 'scope', surface: 'chat', chatId: 'chat-1' }))
      .not.toBe(chatClientStateKey({ account: accountA, scopeId: 'scope', surface: 'browser', chatId: 'chat-1' }));
  });

  it('persists presentation preferences without persisting artifact payloads', () => {
    const storageKey = 'chat-view-test';
    writeChatViewPreferences(storageKey, {
      ...EMPTY_CHAT_VIEW_STATE,
      previewOpen: true,
      todoCollapsed: true,
      activePreviewId: 'artifact-1',
      previewItems: [{
        id: 'artifact-1',
        title: 'Sensitive artifact',
        resource: { schemaVersion: 1, kind: 'interactive', artifactId: 'artifact-1' },
        artifact: {
          kind: 'interactive_artifact',
          artifact_id: 'artifact-1',
          component_type: 'html_preview',
          completion_mode: 'render_only',
          title: 'Sensitive artifact',
          props: { html: '<p>not persisted</p>' },
        },
      }],
    });

    expect(readChatViewPreferences(storageKey)).toEqual({
      explorerOpen: false,
      debugOpen: false,
      previewOpen: true,
      todoCollapsed: true,
      activePreviewId: 'artifact-1',
      previewItems: [],
    });
    expect(window.localStorage.getItem(storageKey)).not.toContain('not persisted');
  });

  it('restores bounded file references without trusting a persisted id', () => {
    const storageKey = 'chat-view-file-preview';
    writeChatViewPreferences(storageKey, {
      ...EMPTY_CHAT_VIEW_STATE,
      previewOpen: true,
      activePreviewId: 'file:chat:chat-1:/data/diagrams/example.drawio',
      previewItems: [{
        id: 'file:chat:chat-1:/data/diagrams/example.drawio',
        title: 'example.drawio',
        resource: {
          schemaVersion: 1,
          kind: 'file',
          fileRef: {
            schemaVersion: 1,
            scope: 'chat',
            chatId: 'chat-1',
            path: '/data/diagrams/example.drawio',
          },
        },
      }],
    });

    const raw = JSON.parse(window.localStorage.getItem(storageKey)!) as {
      previewItems: Array<{ id: string }>;
    };
    raw.previewItems[0].id = 'forged-id';
    window.localStorage.setItem(storageKey, JSON.stringify(raw));

    expect(readChatViewPreferences(storageKey)?.previewItems).toEqual([{
      id: 'file:chat:chat-1:/data/diagrams/example.drawio',
      title: 'example.drawio',
      resource: {
        schemaVersion: 1,
        kind: 'file',
        fileRef: {
          schemaVersion: 1,
          scope: 'chat',
          chatId: 'chat-1',
          path: '/data/diagrams/example.drawio',
        },
      },
    }]);
  });
});
