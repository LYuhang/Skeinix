/**
 * Shared route module loaders.
 *
 * React Router and the navigation shell both use these exact functions. The
 * router turns them into lazy components; the sidebar invokes them on
 * hover/focus so a deliberate navigation can use the browser module cache
 * instead of starting its network waterfall after the click.
 */
export const loadCanvasPage = () => import('@/pages/canvas/CanvasPage');
export const loadAppLayout = () => import('@/app/AppLayout');
export const loadChatPage = () => import('@/pages/chat/ChatPage');
export const loadEmbedChatPage = () => import('@/pages/embed/EmbedChatPage');
export const loadStandalonePreviewPage = () => import('@/pages/preview/StandalonePreviewPage');
export const loadSettingsPage = () => import('@/pages/settings/SettingsPage');
export const loadLoginPage = () => import('@/pages/auth/LoginPage');
export const loadSignupPage = () => import('@/pages/auth/SignupPage');
export const loadResetPasswordPage = () => import('@/pages/auth/ResetPasswordPage');
export const loadWorkspacePage = () => import('@/pages/workspace/WorkspacePage');
export const loadTasksListPage = () => import('@/pages/tasks/TasksListPage');
export const loadTaskDetailPage = () => import('@/pages/tasks/TaskDetailPage');
export const loadDeploymentsListPage = () => import('@/pages/deployments/DeploymentsListPage');
export const loadDeploymentDetailPage = () => import('@/pages/deployments/DeploymentDetailPage');
export const loadMcpServersPage = () => import('@/pages/mcp-servers/McpServersPage');
export const loadMcpServerDetailPage = () => import('@/pages/mcp-servers/McpServerDetailPage');
export const loadMcpCatalogDetailPage = () => import('@/pages/mcp-servers/McpCatalogDetailPage');
export const loadSkillsPage = () => import('@/pages/skills/SkillsPage');
export const loadSkillDetailPage = () => import('@/pages/skills/SkillDetailPage');
export const loadSkillCatalogDetailPage = () => import('@/pages/skills/SkillCatalogDetailPage');
export const loadStoragePage = () => import('@/pages/storage/StoragePage');
export const loadKnowledgeListPage = () => import('@/pages/knowledge/KnowledgeListPage');
export const loadKnowledgeDetailPage = () => import('@/pages/knowledge/KnowledgeDetailPage');
export const loadPlatformManagementPage = () => import('@/pages/management/PlatformManagementPage');

const NAV_ROUTE_LOADERS: Readonly<Record<string, () => Promise<unknown>>> = {
  '/chat': loadChatPage,
  '/preview': loadStandalonePreviewPage,
  '/workspace': loadWorkspacePage,
  '/tasks': loadTasksListPage,
  '/tasks/:taskId': loadTaskDetailPage,
  '/deployments': loadDeploymentsListPage,
  '/deployments/:depId': loadDeploymentDetailPage,
  '/mcp-servers': loadMcpServersPage,
  '/mcp-servers/:id': loadMcpServerDetailPage,
  '/mcp-servers/discover/:source': loadMcpCatalogDetailPage,
  '/skills': loadSkillsPage,
  '/skills/:id': loadSkillDetailPage,
  '/skills/discover/:source': loadSkillCatalogDetailPage,
  '/storage': loadStoragePage,
  '/knowledge': loadKnowledgeListPage,
  '/knowledge/:kbId': loadKnowledgeDetailPage,
  '/workflow/:wfId': loadCanvasPage,
  '/settings': loadSettingsPage,
  '/management': loadPlatformManagementPage,
};

const PRIMARY_IDLE_ROUTES = [
  '/workspace',
  '/tasks',
  '/deployments',
  '/mcp-servers',
  '/skills',
  '/knowledge',
  '/storage',
  '/settings',
] as const;

const DETAIL_IDLE_ROUTES = [
  '/tasks/:taskId',
  '/deployments/:depId',
  '/knowledge/:kbId',
  '/skills/:id',
  '/mcp-servers/:id',
  '/mcp-servers/discover/:source',
  '/skills/discover/:source',
  '/workflow/:wfId',
] as const;

const pendingPreloads = new Map<string, Promise<unknown>>();

/** Preload a top-level route after the user signals navigation intent. */
export function preloadRoute(pathname: string): Promise<unknown> | undefined {
  const loader = NAV_ROUTE_LOADERS[pathname];
  if (!loader) return undefined;
  const existing = pendingPreloads.get(pathname);
  if (existing) return existing;

  const pending = loader().catch(() => {
    // A transient chunk/network failure must remain retryable on click.
    pendingPreloads.delete(pathname);
  });
  pendingPreloads.set(pathname, pending);
  return pending;
}

/**
 * Warm authenticated product surfaces after the current route becomes idle.
 *
 * A pointer hover remains the highest-priority signal. This background pass
 * closes the remaining gap for keyboard navigation and immediate clicks on a
 * cold production server. Primary list pages load first; heavier editor and
 * detail modules only start after those imports settle, so Chat bootstrap is
 * never blocked by optional route code.
 */
export function scheduleAuthenticatedRoutePreloads(currentPathname: string): () => void {
  let cancelled = false;
  let detailTimer: number | null = null;
  const run = () => {
    if (cancelled) return;
    const primary = PRIMARY_IDLE_ROUTES
      .filter((pathname) => pathname !== currentPathname)
      .map((pathname) => preloadRoute(pathname))
      .filter((pending): pending is Promise<unknown> => Boolean(pending));
    void Promise.allSettled(primary).then(() => {
      if (cancelled) return;
      detailTimer = window.setTimeout(() => {
        if (cancelled) return;
        DETAIL_IDLE_ROUTES.forEach((pathname) => { void preloadRoute(pathname); });
      }, 120);
    });
  };

  const idleWindow = window as Window & {
    requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
    cancelIdleCallback?: (id: number) => void;
  };
  let idleId: number | null = null;
  let fallbackTimer: number | null = null;
  if (idleWindow.requestIdleCallback) {
    idleId = idleWindow.requestIdleCallback(run, { timeout: 700 });
  } else {
    fallbackTimer = window.setTimeout(run, 250);
  }
  return () => {
    cancelled = true;
    if (idleId !== null) idleWindow.cancelIdleCallback?.(idleId);
    if (fallbackTimer !== null) window.clearTimeout(fallbackTimer);
    if (detailTimer !== null) window.clearTimeout(detailTimer);
  };
}
