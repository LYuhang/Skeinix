/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_BASE_PATH?: string;
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

interface Window {
  /** May be injected by the serving host before the Vite entry executes. */
  __VIBECANVAS_RUNTIME_CONFIG__?: import('@/lib/base-path').VibeCanvasRuntimeConfig;
}
