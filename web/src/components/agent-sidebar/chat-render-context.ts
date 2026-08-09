import { createContext, useContext } from 'react';

export interface ChatRenderIdentity {
  chatId: string | null;
  surface: 'chat' | 'browser';
}

const ChatRenderContext = createContext<ChatRenderIdentity | null>(null);

export const ChatRenderProvider = ChatRenderContext.Provider;
export const useChatRenderIdentity = () => useContext(ChatRenderContext);
