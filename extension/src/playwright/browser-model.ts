/**
 * Copyright (c) Microsoft Corporation.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

/**
 * Adapted from microsoft/playwright
 * packages/playwright-core/src/tools/mcp/browserModel.ts at commit
 * 680e5ad5894a54bba9e4ed8a311fd2aee388137d.
 *
 * Only the debug logger import was replaced with an injected error callback so
 * this Apache-2.0 upstream model can run in a Chrome MV3 extension bundle.
 */

import type { RelayDebuggee, RelayTab } from "./relay-executor";

export type CDPMessage = {
  id?: number;
  sessionId?: string;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: { code?: number; message: string };
};

export type SendRelayCommand = (method: string, params: unknown[]) => Promise<unknown>;
export type SendToCDPClient = (message: CDPMessage) => void;

type TabSession = {
  tabId: number;
  sessionId: string;
  targetInfo: Record<string, unknown> | undefined;
  childSessions: Set<string>;
};

export class PlaywrightBrowserModel {
  private sendToCDPClient: SendToCDPClient | null = null;
  private readonly knownTabs = new Map<number, RelayTab>();
  private readonly tabSessions = new Map<number, TabSession>();
  private autoAttach = false;
  private nextSessionId = 1;

  constructor(
    private readonly sendToExtension: SendRelayCommand,
    private readonly onUnhandledError: (error: unknown) => void = console.error,
  ) {}

  connectOverCDP(sendToCDPClient: SendToCDPClient): void {
    this.sendToCDPClient = sendToCDPClient;
  }

  disconnectFromCDP(): void {
    this.sendToCDPClient = null;
  }

  private emit(message: CDPMessage): void {
    this.sendToCDPClient?.(message);
  }

  onTabCreated(tab: RelayTab): void {
    if (tab.id === undefined) return;
    this.knownTabs.set(tab.id, tab);
    if (this.autoAttach)
      void this.attachTab(tab.id).catch(this.onUnhandledError);
  }

  onTabRemoved(tabId: number): void {
    this.knownTabs.delete(tabId);
    this.detachTab(tabId);
  }

  onDebuggerEvent(
    source: RelayDebuggee,
    method: string,
    params: Record<string, unknown> | undefined,
  ): void {
    if (source.tabId === undefined) return;
    const tabSession = this.tabSessions.get(source.tabId);
    if (!tabSession) return;
    const childSessionId = String(params?.sessionId || "");
    if (method === "Target.attachedToTarget" && childSessionId)
      tabSession.childSessions.add(childSessionId);
    else if (method === "Target.detachedFromTarget" && childSessionId)
      tabSession.childSessions.delete(childSessionId);
    this.emit({
      sessionId: source.sessionId || tabSession.sessionId,
      method,
      params: params ?? {},
    });
  }

  onDebuggerDetach(source: RelayDebuggee): void {
    if (source.tabId !== undefined) this.detachTab(source.tabId);
  }

  async enableAutoAttach(): Promise<void> {
    this.autoAttach = true;
    await Promise.all(
      [...this.knownTabs.keys()].map((tabId) =>
        this.attachTab(tabId).catch((error) => {
          this.onUnhandledError(error);
          return undefined;
        }),
      ),
    );
  }

  async createTarget(url: string | undefined): Promise<{ targetId: string | undefined }> {
    const tab = (await this.sendToExtension("chrome.tabs.create", [{ url }])) as RelayTab;
    if (tab?.id === undefined) throw new Error("Failed to create tab");
    this.knownTabs.set(tab.id, tab);
    const tabSession = await this.attachTab(tab.id);
    return { targetId: String(tabSession.targetInfo?.targetId || "") || undefined };
  }

  async closeTarget(targetId: string | undefined): Promise<{ success: boolean }> {
    const tabSession = targetId
      ? this.findTabSession((session) => session.targetInfo?.targetId === targetId)
      : undefined;
    if (!tabSession) return { success: false };
    await this.sendToExtension("chrome.tabs.remove", [tabSession.tabId]);
    return { success: true };
  }

  getTargetInfo(sessionId: string | undefined): Record<string, unknown> | undefined {
    if (!sessionId) return undefined;
    return this.findTabSession((session) => session.sessionId === sessionId)?.targetInfo;
  }

  async sendBrowserCommand(method: string, params: unknown): Promise<unknown> {
    const tabSession = this.tabSessions.values().next().value as TabSession | undefined;
    if (!tabSession)
      throw new Error(`No attached tab to forward browser-level command: ${method}`);
    return this.sendToExtension("chrome.debugger.sendCommand", [
      { tabId: tabSession.tabId },
      method,
      params,
    ]);
  }

  async sendCommand(sessionId: string, method: string, params: unknown): Promise<unknown> {
    let tabSession = this.findTabSession((session) => session.sessionId === sessionId);
    let childSessionId: string | undefined;
    if (!tabSession) {
      tabSession = this.findTabSession((session) => session.childSessions.has(sessionId));
      childSessionId = sessionId;
    }
    if (!tabSession) throw new Error(`No tab found for sessionId: ${sessionId}`);
    return this.sendToExtension("chrome.debugger.sendCommand", [
      { tabId: tabSession.tabId, sessionId: childSessionId },
      method,
      params,
    ]);
  }

  private async attachTab(tabId: number): Promise<TabSession> {
    const existing = this.tabSessions.get(tabId);
    if (existing) return existing;
    await this.sendToExtension("chrome.debugger.attach", [{ tabId }, "1.3"]);
    const result = (await this.sendToExtension("chrome.debugger.sendCommand", [
      { tabId },
      "Target.getTargetInfo",
      {},
    ])) as { targetInfo?: Record<string, unknown> } | undefined;
    const targetInfo = result?.targetInfo;
    const sessionId = `pw-tab-${this.nextSessionId++}`;
    const tabSession: TabSession = {
      tabId,
      sessionId,
      targetInfo,
      childSessions: new Set(),
    };
    this.tabSessions.set(tabId, tabSession);
    this.emit({
      method: "Target.attachedToTarget",
      params: {
        sessionId,
        targetInfo: { ...targetInfo, attached: true },
        waitingForDebugger: false,
      },
    });
    return tabSession;
  }

  private detachTab(tabId: number): void {
    const tabSession = this.tabSessions.get(tabId);
    if (!tabSession) return;
    this.tabSessions.delete(tabId);
    this.emit({
      method: "Target.detachedFromTarget",
      params: {
        sessionId: tabSession.sessionId,
        targetId: tabSession.targetInfo?.targetId,
      },
    });
  }

  private findTabSession(
    predicate: (session: TabSession) => boolean,
  ): TabSession | undefined {
    for (const session of this.tabSessions.values()) {
      if (predicate(session)) return session;
    }
    return undefined;
  }
}
