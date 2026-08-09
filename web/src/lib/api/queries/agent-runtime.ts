import { useQuery } from '@tanstack/react-query';

import { getAgentRuntimeCapabilities } from '@/lib/api/agent-runtime';

export const runtimeCapabilitiesPrefix = [
  'agent-runtime',
  'capabilities',
] as const;

export const codexAccountUsageQueryKey = [
  'agent-runtime',
  'codex',
  'account-usage',
] as const;

export const runtimeCapabilitiesKey = (chatId?: string | null) => [
  ...runtimeCapabilitiesPrefix,
  chatId ?? 'new-chat',
] as const;

export function useAgentRuntimeCapabilities(
  chatId?: string | null,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: runtimeCapabilitiesKey(chatId),
    queryFn: () => getAgentRuntimeCapabilities(chatId),
    enabled: options?.enabled ?? true,
    staleTime: 30_000,
    // Global Settings may be changed in another tab. Existing chats remain
    // protected by their server-side Runtime binding, while an unstarted chat
    // should always pick up the latest global default.
    refetchOnWindowFocus: 'always',
  });
}
