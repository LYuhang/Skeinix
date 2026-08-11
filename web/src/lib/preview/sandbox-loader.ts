export const PREVIEW_SANDBOX_LOADER_CHANNEL = 'vibecanvas:interactive-loader:v1';
export const PREVIEW_SANDBOX_LOADER_PATH = '/interactive-sandbox.html';

export interface PreviewSandboxLoaderMessage {
  channel: typeof PREVIEW_SANDBOX_LOADER_CHANNEL;
  type: 'ready' | 'error';
  message?: string;
}

export function isPreviewSandboxLoaderMessage(
  value: unknown,
): value is PreviewSandboxLoaderMessage {
  if (!value || typeof value !== 'object') return false;
  const message = value as Record<string, unknown>;
  return message.channel === PREVIEW_SANDBOX_LOADER_CHANNEL
    && (message.type === 'ready' || message.type === 'error')
    && (message.message === undefined || typeof message.message === 'string');
}

export function loadPreviewSandboxDocument(
  frame: HTMLIFrameElement | null,
  html: string,
): void {
  if (!frame?.contentWindow || !html) return;
  frame.contentWindow.postMessage({
    channel: PREVIEW_SANDBOX_LOADER_CHANNEL,
    type: 'load',
    html,
  }, '*');
}
