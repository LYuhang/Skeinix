import { beforeEach, describe, expect, it } from 'vitest';
import {
  MAX_INACTIVE_CHAT_RUNTIMES,
  useChatStreamStore,
} from '@/stores/chat-stream';

describe('chat stream retention limits', () => {
  beforeEach(() => useChatStreamStore.getState().reset());

  it('keeps only the most recently touched inactive chat runtimes', () => {
    for (let index = 0; index < MAX_INACTIVE_CHAT_RUNTIMES + 7; index += 1) {
      const chatId = `chat-${index}`;
      useChatStreamStore.getState().beginTurn(chatId, `turn-${index}`);
      useChatStreamStore.getState().setState('complete', chatId);
    }

    const runtimes = useChatStreamStore.getState().runtimes;
    expect(Object.keys(runtimes)).toHaveLength(MAX_INACTIVE_CHAT_RUNTIMES);
    expect(runtimes['chat-0']).toBeUndefined();
    expect(runtimes[`chat-${MAX_INACTIVE_CHAT_RUNTIMES + 6}`]).toBeDefined();
  });

  it('never evicts live turns while trimming completed runtimes', () => {
    useChatStreamStore.getState().beginTurn('live-a', 'turn-live-a');
    useChatStreamStore.getState().beginTurn('live-b', 'turn-live-b');

    for (let index = 0; index < MAX_INACTIVE_CHAT_RUNTIMES + 4; index += 1) {
      const chatId = `done-${index}`;
      useChatStreamStore.getState().beginTurn(chatId, `turn-${index}`);
      useChatStreamStore.getState().setState('complete', chatId);
    }

    const runtimes = useChatStreamStore.getState().runtimes;
    expect(runtimes['live-a']?.state).toBe('streaming');
    expect(runtimes['live-b']?.state).toBe('streaming');
    expect(Object.values(runtimes).filter((runtime) => runtime.state !== 'streaming'))
      .toHaveLength(MAX_INACTIVE_CHAT_RUNTIMES);
  });
});
