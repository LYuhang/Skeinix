/**
 * Dynamic config-option sources for the node config editors (Stream 3).
 *
 * Some option lists are NOT static — they come from the user's
 * Settings/backend and must be fetched live + cached so newly-added
 * options appear without a reload. This module is the pluggable
 * indirection an editor uses to declare *what* it needs (e.g. "the
 * model list") without knowing *where* it comes from.
 *
 * ## Data sources (today)
 *
 * - **`model` → PromptNode `model_name`.** The user's configured LLMs
 *   (BYO-LLM) are surfaced by the backend `GET /api/v1/enums`
 *   `model_names` list. That list is produced by
 *   `enums.get_frontend_enums()` from the live `llm_registry`
 *   (`api/src/vibecanvas_api/enums.py:146` → `get_prompt_models()`),
 *   which `load_config_and_sync` snapshots from the tenant's configured
 *   providers at startup. There is **no separate per-tenant model
 *   endpoint** in this repo — `model_names` IS the configured-model list,
 *   so `useModelOptions` reads it. We re-export it through this hook
 *   rather than hardcoding `getEnumList(enums,'model_names')` at the
 *   editor so the source stays swappable when a dedicated
 *   `/settings/models` endpoint lands.
 *
 * The hook reuses the shared `useEnums()` react-query cache (10-min
 * staleTime); it refetches with the rest of the enums payload, so a
 * model added server-side appears on the next cache refresh. Graceful
 * fallback: an empty list when the query is loading / the key is absent.
 *
 * ## Pluggability
 *
 * Editors currently consume the model list through `useModelOptions`.
 */
import { getEnumList, useEnums } from '@/lib/api/queries/enums';

export interface ConfigOptions {
  /** The option values (also their labels for now — all enum lists are flat strings). */
  options: string[];
  isLoading: boolean;
}

/**
 * Live list of the tenant's configured LLM model names (BYO-LLM).
 *
 * Source: `GET /api/v1/enums` → `model_names` (the live `llm_registry`
 * snapshot). Cached via `useEnums`; refetches so newly-configured models
 * appear. Returns `[]` while loading / when none are configured (the
 * PromptNode editor falls back to a free-text entry in that case).
 */
export function useModelOptions(): ConfigOptions {
  const { data: enums, isLoading } = useEnums();
  return { options: getEnumList(enums, 'model_names'), isLoading };
}
