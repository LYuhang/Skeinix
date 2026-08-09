export interface ChatAccountIdentity {
  tenant_id?: string | null;
  user_id?: string | null;
}

export function chatAccountNamespace(identity: ChatAccountIdentity | null | undefined): string {
  if (!identity) return 'anonymous';
  return `${identity.tenant_id || 'tenant-pending'}:${identity.user_id || 'user-pending'}`;
}

export function chatClientStateKey({
  account,
  scopeId,
  surface,
  chatId,
  suffix,
}: {
  account: ChatAccountIdentity | null | undefined;
  scopeId: string;
  surface: 'chat' | 'browser';
  chatId: string;
  suffix?: string;
}): string {
  return [
    chatAccountNamespace(account),
    scopeId || 'scope-pending',
    surface,
    chatId || 'draft',
    suffix,
  ].filter(Boolean).join(':');
}
