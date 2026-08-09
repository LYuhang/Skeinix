import type { MergedToolCall } from '../types';
import type { ToolEnvelope } from './parseEnvelope';
import { rendererFor } from './renderer-utils';

export type ToolPresenterKey =
  | 'terminal'
  | 'diff'
  | 'browser'
  | 'envelope'
  | 'universal'
  | 'plain-text';

const TRUSTED_ORIGINS = new Set([
  'builtin',
  'runtime_native',
  'platform_mcp',
  // Historical envelopes emitted before origin became structured.
  'runtime_builtin',
]);

function originKind(call: MergedToolCall): string {
  const origin = call.invocation?.origin;
  return typeof origin === 'string' ? origin : origin?.kind ?? '';
}

/**
 * Custom MCP names and presentation hints are untrusted. They always use the
 * bounded standard-content renderer, even when they intentionally collide
 * with a built-in name such as `bash` or `edit_file`.
 */
export function isTrustedToolPresentation(call: MergedToolCall): boolean {
  return !call.invocation || TRUSTED_ORIGINS.has(originKind(call));
}

function semanticPresenter(call: MergedToolCall): ToolPresenterKey | null {
  const capability = call.invocation?.capability.toLowerCase() ?? '';
  const hint = call.invocation?.presentation?.kind?.toLowerCase() ?? '';
  const name = call.name.toLowerCase();

  if (hint === 'terminal' || /(?:^|[._-])(shell|terminal|command)(?:$|[._-])/.test(capability)) {
    return 'terminal';
  }
  if (hint === 'diff' || /(?:^|[._-])(edit|patch|diff)(?:$|[._-])/.test(capability)) {
    return 'diff';
  }
  if (hint === 'browser' || capability.startsWith('browser.') || name.startsWith('browser_')) {
    return 'browser';
  }
  if (['bash', 'shell', 'run_command', 'execute_command'].includes(name)) return 'terminal';
  if (['edit_file', 'apply_patch', 'patch_file'].includes(name)) return 'diff';
  return null;
}

/** Collision-safe priority: trusted semantic capability → normalized MIME →
 * standard MCP content → plain text. Runtime adapters provide semantics; the
 * registry alone chooses the visual presenter. */
export function selectToolPresenter({
  call,
  envelope,
  hasUniversal,
}: {
  call: MergedToolCall;
  envelope: ToolEnvelope | null;
  hasUniversal: boolean;
}): ToolPresenterKey {
  if (!isTrustedToolPresentation(call)) return hasUniversal ? 'universal' : 'plain-text';

  const semantic = semanticPresenter(call);
  if (semantic === 'browser' && envelope) return 'browser';
  if (semantic === 'terminal' && envelope?.output) return 'terminal';
  if (semantic === 'diff' && envelope?.output && typeof envelope.output.data === 'string') return 'diff';

  if (envelope) {
    const mimePresenter = rendererFor(envelope.output?.content_type);
    if (mimePresenter === 'terminal' || mimePresenter === 'diff') return mimePresenter;
    return 'envelope';
  }
  return hasUniversal ? 'universal' : 'plain-text';
}
