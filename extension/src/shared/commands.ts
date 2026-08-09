// Mirror of api/src/vibecanvas_api/browser/commands.py (R-C). Keep the string
// values byte-identical with the Python enum. parseCommand is the extension-side
// closed-enum gate: any cmd not in CMD is rejected before a handler runs (§6).
import { encode, type Envelope } from "./envelope";

export const CMD = {
  NAVIGATE: "navigate",
  SNAPSHOT: "snapshot",
  READ_TEXT: "read_text",
  READ_FIELDS: "read_fields",
  QUERY: "query",
  GET_ATTRIBUTE: "get_attribute",
  GET_HTML: "get_html",
  SCREENSHOT: "screenshot",
  GET_IMAGE: "get_image",
  ACQUIRE_VIDEO: "acquire_video",
  FETCH_RESOURCE: "fetch_resource",
  SCROLL: "scroll",
  WAIT_FOR: "wait_for",
  LIST_TABS: "list_tabs",
  SWITCH_TAB: "switch_tab",
  CLOSE_TAB: "close_tab",
  WAIT_FOR_NEW_TAB: "wait_for_new_tab",
  LIST_OPEN_TABS: "list_open_tabs",
  USE_TAB: "use_tab",
  CLICK: "click",
  TYPE: "type",
  FILL: "fill",
  SELECT: "select",
  PRESS: "press",
  SUBMIT: "submit",
  ASSERT: "assert",
  HIGHLIGHT: "highlight",
  NARRATE: "narrate",
  CHECK_LOGIN: "check_login",
  START_SESSION: "start_session",
  END_SESSION: "end_session",
} as const;

export type Cmd = (typeof CMD)[keyof typeof CMD];
export const ALL_CMDS: Cmd[] = Object.values(CMD);
const _set = (...xs: Cmd[]) => new Set<Cmd>(xs);

export const ACT_CMDS: ReadonlySet<Cmd> = _set(
  CMD.CLICK,
  CMD.TYPE,
  CMD.FILL,
  CMD.SELECT,
  CMD.PRESS,
  CMD.SUBMIT,
);
export const CONTROL_CMDS: ReadonlySet<Cmd> = _set(
  CMD.NAVIGATE,
  CMD.SWITCH_TAB,
  CMD.CLOSE_TAB,
  CMD.USE_TAB,
  CMD.START_SESSION,
  CMD.END_SESSION,
);
// The side-effect boundary is broader than page writes: navigation, tab
// selection/closing/adoption, and browser-session lifecycle also change browser
// state and must be treated as risk-bearing operations by authorization/audit.
export const MUTATING: ReadonlySet<Cmd> = new Set<Cmd>([
  ...ACT_CMDS,
  ...CONTROL_CMDS,
]);

export type CommandMsg = {
  cmd: Cmd;
  args: Record<string, unknown>;
  target_id: string;
};

export function parseCommand(env: Envelope): CommandMsg {
  if (env.kind !== "command") throw new Error(`not a command: kind=${env.kind}`);
  const d = (env.data ?? {}) as Record<string, unknown>;
  if (!ALL_CMDS.includes(d.cmd as Cmd)) {
    throw new Error(`unknown command: ${String(d.cmd)}`);
  }
  // target_id is OPTIONAL: an empty value means "the controlled root tab" — the
  // router resolves it to sm.knownTargets()[0]. Session/global commands
  // (start_session, check_login, list_tabs, wait_for_new_tab) and any
  // single-tab op send no target_id; throwing here broke ALL of them (the tab
  // attached via ensureAttached, but the command itself never ran).
  return {
    cmd: d.cmd as Cmd,
    args: (d.args ?? {}) as Record<string, unknown>,
    target_id: d.target_id ? String(d.target_id) : "",
  };
}

export function makeObservation(
  id: string,
  channel: string,
  transport: string,
  data: {
    ok: boolean;
    target_id: string;
    error?: string;
    media?: unknown[];
    [k: string]: unknown;
  },
): string {
  return encode("observation", { id, channel, transport, data });
}
