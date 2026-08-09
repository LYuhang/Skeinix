// Inbound-command router (§4.1 thin plugin). parseCommand (closed-enum gate) →
// resolve the TARGET (a tab) → dispatch the bundled handler → makeObservation
// echoing the command id + the STABLE tab + internal target_id → sendObservation.
//
// Tab addressing: the agent addresses a tab by its STABLE `tab` (tabId, fixed for
// the tab's life), passed in `args.tab`. We resolve it to the live targetId here
// (the targetId changes on every navigation). No `args.tab` → the controlled root
// tab. The observation echoes `tab` (stable) so the agent always gets a handle it
// can reuse across navigations.
//
// On any handler throw it still replies ok:false so the host never hangs. Media
// bytes stay base64 in data.media[*].b64 here; the host strips + VFS-writes them.
import { type Envelope } from "../shared/envelope";
import { parseCommand, makeObservation, CMD, MUTATING, type Cmd } from "../shared/commands";
import { dispatch, type Overlay } from "./handlers";
import type { SessionManager } from "./session-manager";

export async function routeCommand(
  env: Envelope,
  deps: {
    sm: SessionManager;
    ov: Overlay;
    sendObservation: (raw: string) => void;
  },
): Promise<void> {
  const { sm, ov, sendObservation } = deps;
  let data: Record<string, unknown>;
  let targetId = "";
  let tab: number | undefined;
  let commandId = env.id;
  let parsedCmd: Cmd | undefined;
  let dispatchStarted = false;
  try {
    const c = parseCommand(env);
    parsedCmd = c.cmd;
    commandId = typeof c.args.command_id === "string" ? c.args.command_id : env.id;
    // Resolve the target: `args.tab` (stable tabId) wins → live targetId; else
    // an explicit internal target_id; else the controlled root tab.
    const tabArg = c.args.tab;
    if (c.cmd === CMD.USE_TAB || c.cmd === CMD.LIST_OPEN_TABS) {
      // These operate OUTSIDE the controlled session: `use_tab` ADOPTS a tab the
      // session doesn't control yet, and `list_open_tabs` enumerates the user's
      // own tabs. Don't resolve `args.tab` against the session here — the handler
      // takes the raw chrome tab id and (for use_tab) attaches it.
      targetId = "";
    } else if (tabArg != null && tabArg !== "") {
      const resolved = sm.targetForTab(Number(tabArg));
      if (!resolved) throw new Error(`tab ${tabArg} is not controlled`);
      targetId = resolved;
    } else {
      targetId = c.target_id || sm.rootTarget() || sm.knownTargets()[0] || "";
    }
    tab = sm.tabIdFor(targetId);
    dispatchStarted = true;
    const out = await dispatch(c.cmd, sm, ov, targetId, c.args);
    data = { ok: out.ok ?? true, tab, target_id: targetId, command_id: commandId, ...out };
    // Handler-level ok:false responses are validation/precondition failures. A
    // mutating command has not crossed its effect boundary in these paths.
    if (out.ok === false && MUTATING.has(c.cmd)) {
      data.error_code ??= "browser_command_not_executed";
      data.not_executed ??= true;
      data.error_info ??= { not_executed: true };
    }
  } catch (e: any) {
    const message = String(e?.message || e);
    const tabNotControlled = /^tab .* is not controlled$/.test(message);
    const effectUnknown = !!parsedCmd && dispatchStarted && MUTATING.has(parsedCmd);
    data = {
      ok: false,
      tab,
      target_id: targetId,
      command_id: commandId,
      error: message,
      ...(effectUnknown
        ? {
            error_code: "browser_command_result_unknown",
            effect_status: "unknown",
            error_info: { effect_status: "unknown" },
          }
        : tabNotControlled
        ? {
            error_code: "tab_not_controlled",
            not_executed: true,
            error_info: { not_executed: true },
          }
        : {
            error_code: "browser_command_not_executed",
            not_executed: true,
            error_info: { not_executed: true },
          }),
    };
  }
  sendObservation(
    makeObservation(env.id, env.channel, env.transport, data as any),
  );
}
