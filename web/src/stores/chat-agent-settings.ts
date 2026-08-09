import { create } from 'zustand';

import { getAgentSettings, type AgentSettings } from '@/stores/agent-settings';

interface ChatAgentSettingsEntry {
  settings: AgentSettings;
  locked: boolean;
}

interface ChatAgentSettingsState {
  entries: Record<string, ChatAgentSettingsEntry>;
  initializeDraft: (chatId: string) => void;
  hydrateLocked: (chatId: string, settings: AgentSettings) => void;
  set: (chatId: string, patch: Partial<AgentSettings>) => void;
}

export const useChatAgentSettingsStore = create<ChatAgentSettingsState>((set) => ({
  entries: {},
  initializeDraft: (chatId) => set((state) => state.entries[chatId]
    ? state
    : {
        entries: {
          ...state.entries,
          [chatId]: { settings: getAgentSettings(), locked: false },
        },
      }),
  hydrateLocked: (chatId, settings) => set((state) => ({
    entries: {
      ...state.entries,
      [chatId]: { settings, locked: true },
    },
  })),
  set: (chatId, patch) => set((state) => {
    const current = state.entries[chatId] ?? {
      settings: getAgentSettings(),
      locked: false,
    };
    if (current.locked) return state;
    return {
      entries: {
        ...state.entries,
        [chatId]: {
          ...current,
          settings: { ...current.settings, ...patch },
        },
      },
    };
  }),
}));

export function getChatAgentSettings(chatId: string): AgentSettings {
  return useChatAgentSettingsStore.getState().entries[chatId]?.settings
    ?? getAgentSettings();
}
