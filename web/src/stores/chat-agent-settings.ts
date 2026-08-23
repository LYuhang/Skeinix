import { create } from 'zustand';

import { getAgentSettings, type AgentSettings } from '@/stores/agent-settings';

interface ChatAgentSettingsEntry {
  settings: AgentSettings;
}

interface ChatAgentSettingsState {
  entries: Record<string, ChatAgentSettingsEntry>;
  initializeDraft: (chatId: string) => void;
  hydrateBound: (chatId: string, settings: AgentSettings) => void;
  set: (chatId: string, patch: Partial<AgentSettings>) => void;
}

export const useChatAgentSettingsStore = create<ChatAgentSettingsState>((set) => ({
  entries: {},
  initializeDraft: (chatId) => set((state) => state.entries[chatId]
    ? state
    : {
        entries: {
          ...state.entries,
          [chatId]: { settings: getAgentSettings() },
        },
      }),
  hydrateBound: (chatId, settings) => set((state) => state.entries[chatId]
    ? state
    : {
        entries: {
          ...state.entries,
          [chatId]: { settings },
        },
      }),
  set: (chatId, patch) => set((state) => {
    const current = state.entries[chatId] ?? {
      settings: getAgentSettings(),
    };
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
