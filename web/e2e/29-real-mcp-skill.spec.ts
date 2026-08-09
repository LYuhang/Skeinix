import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

const session = new E2ECookieSession();
const unique = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
const mcpName = `Real MCP ${unique}`;
const mcpPrefix = `real_${unique.toLowerCase().replace(/[^a-z0-9]/g, '').slice(-20)}`;
const skillName = `real-skill-${unique.toLowerCase().replace(/_/g, '-')}`;
const tempDir = mkdtempSync(join(tmpdir(), 'vibecanvas-real-mcp-skill-'));
const skillZip = join(tempDir, 'skill.zip');
let mcpId: string | undefined;
let skillId: string | undefined;

const MCP_SOURCE = `
from mcp.server.fastmcp import FastMCP

server = FastMCP("Skeinix real acceptance")

@server.tool()
def echo_acceptance(value: str) -> str:
    """Return a deterministic acceptance marker with the supplied value."""
    return "MCP_REAL_OK:" + value

server.run(transport="stdio")
`;

test.setTimeout(720_000);

test.beforeAll(async () => {
  await session.register('mcp-skill-real-e2e');
  await session.api('/api/v1/agent-runtime/settings', {
    method: 'PUT',
    body: JSON.stringify({ default_runtime_type: 'langchain' }),
  });

  const mcpResponse = await session.api('/api/v1/mcp-servers', {
    method: 'POST',
    body: JSON.stringify({
      name: mcpName,
      tool_prefix: mcpPrefix,
      transport: 'stdio',
      endpoint: '/tmp/vibecanvas-runtime-python/env/bin/python',
      description: 'Deterministic real E2E MCP server.',
      connection_config: {
        command: '/tmp/vibecanvas-runtime-python/env/bin/python',
        args: ['-c', MCP_SOURCE],
      },
      auth_config: { type: 'none' },
    }),
  });
  const mcp = await mcpResponse.json() as { id: string };
  mcpId = mcp.id;

  const skillMd = `---
name: ${skillName}
description: Use this skill when asked for the real progressive-disclosure acceptance phrase.
version: 1
allowed-tools: []
---

# Real progressive disclosure acceptance

After loading this skill, reply with the exact marker SKILL_REAL_OK.
Do not emit that marker before this playbook has been loaded through load_skill.
`;
  writeFileSync(join(tempDir, 'SKILL.md'), skillMd, 'utf8');
  execFileSync(
    '/tmp/vibecanvas-runtime-python/env/bin/python',
    [
      '-c',
      'import sys,zipfile; z=zipfile.ZipFile(sys.argv[1],"w",zipfile.ZIP_DEFLATED); z.write(sys.argv[2],"SKILL.md"); z.close()',
      skillZip,
      join(tempDir, 'SKILL.md'),
    ],
  );
  const archive = new Uint8Array(readFileSync(skillZip));
  const form = new FormData();
  form.append('bundle', new Blob([archive], { type: 'application/zip' }), 'skill.zip');
  const skillResponse = await session.form('/api/v1/skills/custom', form);
  const skill = await skillResponse.json() as { id: string };
  skillId = skill.id;
});

test.afterAll(async () => {
  try {
    if (skillId) {
      await session.api(`/api/v1/skills/${skillId}`, { method: 'DELETE' });
    }
    if (mcpId) {
      await session.api(`/api/v1/mcp-servers/${mcpId}`, { method: 'DELETE' });
    }
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
});

test.beforeEach(async ({ context }: { context: BrowserContext }) => {
  await session.seed(context, 'en');
});

async function openNewChat(page: Page) {
  await page.goto('/chat');
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({ timeout: 30_000 });
  await page.locator('[data-action="chat-new"]').click();
}

async function sendAndWait(page: Page, prompt: string, marker: string) {
  const composer = page.locator('[data-role="agent-composer-input"]');
  await composer.fill(prompt);
  await expect(composer).toHaveValue(prompt);
  await expect(page.locator('[data-action="agent-composer-send"]')).toBeEnabled({ timeout: 30_000 });
  const accepted = page.waitForResponse((response) => (
    response.request().method() === 'POST'
    && /\/api\/v1\/chat-scopes\/[^/]+\/chats\/[^/]+\/messages$/.test(new URL(response.url()).pathname)
  ), { timeout: 30_000 });
  await page.locator('[data-action="agent-composer-send"]').click();
  const response = await accepted;
  if (!response.ok()) {
    throw new Error(`Agent Turn rejected: ${response.status()} ${await response.text()}`);
  }
  await expect(
    page.locator('[data-message-role="assistant"]').filter({ hasText: marker }).last(),
  ).toBeVisible({ timeout: 360_000 });
}

test('registers and invokes a real MCP, then loads a real custom Skill', async ({ page }) => {
  if (!mcpId) throw new Error('MCP setup did not return an id');
  await page.goto(`/mcp-servers/${mcpId}`);
  await expect(page.getByText(mcpName, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await page.getByRole('tab', { name: /Tools 1/i }).click();
  await expect(page.getByText('echo_acceptance', { exact: true }).first()).toBeVisible({ timeout: 60_000 });

  await openNewChat(page);
  await page.locator('[data-role="chat-composer-options-toggle"]').click();
  await page.locator('[data-role="chat-mcp-picker"]').click();
  await page.getByText(mcpName, { exact: true }).last().click();
  await expect(page.locator('[data-role="chat-mcp-picker"]')).toContainText('1 MCP');
  await page.keyboard.press('Escape');
  await sendAndWait(
    page,
    `Call ${mcpPrefix}__echo_acceptance exactly once with value "browser". `
      + 'Only after the tool succeeds, reply with its complete result.',
    'MCP_REAL_OK:browser',
  );
  await expect(page.getByText(new RegExp(`${mcpPrefix}__echo_acceptance|echo_acceptance`)).last()).toBeVisible();

  await page.locator('[data-action="chat-new"]').click();
  await sendAndWait(
    page,
    `Use the installed skill named ${skillName}. You must call load_skill with that exact name, `
      + 'follow its playbook, and then return its required marker.',
    'SKILL_REAL_OK',
  );
  await expect(page.getByText(/load_skill/).last()).toBeVisible();

  await page.goto('/skills');
  await expect(page.getByText(skillName, { exact: true }).first()).toBeVisible({ timeout: 60_000 });
});
