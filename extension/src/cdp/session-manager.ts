// Multi-tab session/target manager (§9). The controlled entity is a SESSION OF
// TABS rooted at the attached tab; spawned tabs auto-attach with
// waitForDebuggerOnStart so a click that opens a detail page can't escape
// control (attach-race defense). Errors close excursion tabs (no tab leaks).
//
// `Debugger` is a thin seam over chrome.debugger so this is unit-testable.
export interface Debugger {
  attach(tabId: number): Promise<void>;
  detach(tabId: number): Promise<void>;
  sendCommand(
    target: { tabId: number },
    method: string,
    params?: object,
    sessionId?: string,
  ): Promise<any>;
  onEvent(
    cb: (source: { tabId?: number }, method: string, params: any) => void,
  ): void;
  /** chrome.debugger.getTargets() — used to resolve an auto-attached child
   *  target's REAL chrome tabId (its flat-mode session rides the root, so we
   *  can't infer it from the attach event). */
  getTargets(): Promise<
    Array<{ id: string; tabId?: number; type: string; attached?: boolean }>
  >;
}

export type TabEvent = {
  kind: "new-tab" | "tab-closed" | "detached";
  target_id: string;
  url?: string;
};

type Target = {
  targetId: string;
  /** chrome.debugger SEND address. For an auto-attached child this stays the
   *  ROOT tabId (the flat-mode session rides the root connection); the child is
   *  reached via root tabId + sessionId. */
  tabId: number;
  sessionId?: string;
  url: string;
  /** OUTWARD stable id the AGENT addresses (`tab`). The REAL chrome tabId — for
   *  the root it equals tabId; for a child it's its own tab (resolved via
   *  getTargets), so sub-tabs are independently addressable (not collapsed onto
   *  the root). */
  tab: number;
};

export class SessionManager {
  private rootTabId = -1;
  private rootTargetId = "";
  private byTarget = new Map<string, Target>();
  private cbs: ((e: TabEvent) => void)[] = [];

  constructor(private dbg: Debugger) {
    this.dbg.onEvent((source, method, params) =>
      this.onCdp(source, method, params),
    );
  }

  onTabEvent(cb: (e: TabEvent) => void): void {
    this.cbs.push(cb);
  }
  private emit(e: TabEvent): void {
    this.cbs.forEach((c) => c(e));
  }

  async attachRoot(tabId: number): Promise<string> {
    try {
      await this.dbg.attach(tabId);
    } catch (error) {
      // chrome.debugger attachments can outlive an MV3 service worker. After
      // Chrome starts a fresh worker, its SessionManager is empty while the
      // extension still owns the debugger target. Adopt that exact attachment;
      // never swallow a genuine conflict with another debugger/client.
      const targets = await this.dbg.getTargets().catch(() => []);
      const stillOwnedByExtension = targets.some(
        (target) => target.tabId === tabId && target.attached === true,
      );
      if (!stillOwnedByExtension) throw error;
    }
    // auto-attach to children, freezing them until we resume (attach-race, §9)
    await this.dbg.sendCommand({ tabId }, "Target.setAutoAttach", {
      autoAttach: true,
      waitForDebuggerOnStart: true,
      flatten: true,
    });
    const info = await this.dbg.sendCommand({ tabId }, "Target.getTargetInfo");
    const targetId = info?.targetInfo?.targetId ?? `tab${tabId}`;
    this.rootTabId = tabId;
    this.rootTargetId = targetId;
    this.byTarget.set(targetId, {
      targetId,
      tabId,
      url: info?.targetInfo?.url ?? "",
      tab: tabId, // root: outward tab == tabId
    });
    return targetId;
  }

  /** The OUTWARD stable tab id (real chrome tabId) for a target. */
  tabIdFor(targetId: string): number | undefined {
    return this.byTarget.get(targetId)?.tab;
  }
  /** Reverse of tabIdFor: resolve a STABLE outward tab id to its CURRENT attached
   *  target. The tab id is fixed for the tab's life; its targetId changes on every
   *  (cross-process) navigation — so the agent addresses a tab by its id and we
   *  re-resolve to the live targetId here. Undefined if that tab isn't attached. */
  targetForTab(tab: number): string | undefined {
    if (tab === this.rootTabId && this.rootTargetId) return this.rootTargetId;
    for (const [tid, t] of this.byTarget) {
      if (t.tab === tab) return tid;
    }
    return undefined;
  }
  /** All controlled tabs as stable {tab, url} (deduped by outward tab id; the
   *  root + any excursion tabs the session auto-attached). */
  knownTabs(): { tab: number; url: string }[] {
    const seen = new Map<number, string>();
    for (const t of this.byTarget.values()) {
      if (!seen.has(t.tab)) seen.set(t.tab, t.url);
    }
    return [...seen].map(([tab, url]) => ({ tab, url }));
  }
  /** Resolve an auto-attached child's REAL chrome tabId (async) and update its
   *  outward `tab`, so sub-tabs become independently addressable instead of being
   *  collapsed onto the root. Best-effort: keep the placeholder on failure. */
  private async resolveChildTab(targetId: string): Promise<void> {
    try {
      const targets = await this.dbg.getTargets();
      const info = targets.find((x) => x.id === targetId);
      const entry = this.byTarget.get(targetId);
      if (entry && info && typeof info.tabId === "number" && info.tabId >= 0) {
        entry.tab = info.tabId;
      }
    } catch {
      /* keep the root placeholder */
    }
  }
  knownTargets(): string[] {
    return [...this.byTarget.keys()];
  }
  hasTab(tab: number): boolean {
    return this.knownTabs().some((x) => x.tab === tab);
  }
  removeTab(tab: number): boolean {
    let removed = false;
    for (const [targetId, target] of [...this.byTarget]) {
      if (target.tab === tab || target.tabId === tab) {
        this.byTarget.delete(targetId);
        removed = true;
        this.emit({ kind: "tab-closed", target_id: targetId, url: target.url });
      }
    }
    if (removed && this.rootTabId === tab) {
      const next = this.byTarget.values().next().value as Target | undefined;
      if (next) {
        this.rootTabId = next.tabId;
        this.rootTargetId = next.targetId;
      } else {
        this.rootTabId = -1;
        this.rootTargetId = "";
      }
    }
    return removed;
  }
  rootTab(): number {
    return this.rootTabId;
  }
  rootTarget(): string {
    return this.rootTargetId;
  }
  /** Clear all session state (e.g. after chrome.debugger detached the root). */
  reset(): void {
    this.byTarget.clear();
    this.rootTabId = -1;
    this.rootTargetId = "";
  }

  async send(targetId: string, method: string, params?: object): Promise<any> {
    const t = this.byTarget.get(targetId);
    if (!t) throw new Error(`no such target ${targetId}`);
    return this.dbg.sendCommand({ tabId: t.tabId }, method, params, t.sessionId);
  }

  async closeExcursion(targetId: string): Promise<void> {
    if (targetId === this.rootTargetId) return; // never close the root here
    const target = this.byTarget.get(targetId);
    if (!target) return;
    await this.dbg.sendCommand(
      { tabId: target.tabId },
      "Target.closeTarget",
      { targetId },
      undefined,
    );
    this.byTarget.delete(targetId);
  }

  private onCdp(
    source: { tabId?: number },
    method: string,
    p: any,
  ): void {
    if (
      method === "Target.attachedToTarget" &&
      p?.targetInfo?.type === "page"
    ) {
      const { targetId, url } = p.targetInfo;
      // With several independently attached root tabs, chrome.debugger emits
      // all events through the same listener. Preserve the source root so a
      // popup spawned from an older controlled tab is not accidentally routed
      // through whichever root was attached most recently.
      const sourceTabId =
        typeof source.tabId === "number" ? source.tabId : this.rootTabId;
      this.byTarget.set(targetId, {
        targetId,
        tabId: sourceTabId, // send address: source root tabId + child sessionId
        sessionId: p.sessionId,
        url,
        tab: sourceTabId, // placeholder until resolveChildTab finds the real id
      });
      // Resolve the child's REAL chrome tabId so it's addressable on its own.
      void this.resolveChildTab(targetId);
      if (p.waitingForDebugger) {
        // resume the frozen new target, THEN report it (race-safe)
        this.dbg
          .sendCommand(
            { tabId: sourceTabId },
            "Runtime.runIfWaitingForDebugger",
            undefined,
            p.sessionId,
          )
          .catch(() => {});
      }
      this.emit({ kind: "new-tab", target_id: targetId, url });
    } else if (method === "Target.detachedFromTarget") {
      const id = p?.targetId;
      if (id && this.byTarget.delete(id)) {
        this.emit({ kind: "tab-closed", target_id: id });
      }
    }
  }
}
