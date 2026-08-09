import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, relative, resolve, sep, delimiter } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  expect,
  test,
  type BrowserContext,
  type Locator,
  type Page,
  type TestInfo,
} from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

type RuntimeName = 'langchain' | 'codex';
const CODEX_ACCEPTANCE_AUTH = process.env.VIBECANVAS_CODEX_ACCEPTANCE_AUTH ?? 'account';
if (!['account', 'api'].includes(CODEX_ACCEPTANCE_AUTH)) {
  throw new Error('VIBECANVAS_CODEX_ACCEPTANCE_AUTH must be account or api');
}
type FileEvidence = {
  filename: string;
  bytes: number;
  sha256: string;
  revision: string | null;
};
type ExportEvidence = FileEvidence & {
  format: 'svg' | 'png' | 'pdf';
  theme: 'light';
  background: 'white';
};
type ReviewOutcome = {
  accepted: boolean;
  mode: 'deliver' | 'bounded_warning' | 'incomplete';
  review_count: number;
  warnings: string[];
};
type Bounds = { x: number; y: number; width: number; height: number };
type SceneNode = { id: string; label: string; bounds: Bounds };
type Scene = {
  family: string;
  diagramType: string;
  bounds: Bounds;
  nodes: SceneNode[];
  edges: Array<{
    id: string;
    source: string;
    target: string;
    points: Array<{ x: number; y: number }>;
  }>;
  issues: Array<{ code: string; severity: string }>;
};
type Descriptor = {
  revision: string;
  diagram: { scene: Scene; sourceHash: string };
};
type Fixture = {
  id: string;
  family: string;
  type: string;
  create_prompt: string;
  create_assertions: {
    required_elements: string[];
    required_relations: string[];
  };
  modify_prompt: string;
  modify_assertions: {
    added_elements: string[];
    removed_or_replaced_elements: string[];
    preserved_elements: string[];
    preserve_stable_ids: boolean;
    preserve_mental_map: boolean;
  };
  preview_assertions: Record<string, boolean>;
  visual_question: string;
};
type Manifest = {
  registry_version: string;
  fixtures: Fixture[];
  matrix: Array<{
    fixture_id: string;
    family: string;
    type: string;
    runtime: RuntimeName;
    stage: 'create' | 'modify';
  }>;
};

const currentFile = fileURLToPath(import.meta.url);
const repoRoot = resolve(dirname(currentFile), '../..');
const fixturePath = join(repoRoot, 'web/e2e/fixtures/diagram-acceptance.json');
const python = process.env.VIBECANVAS_PYTHON ?? 'python';
const pythonPath = [
  join(repoRoot, 'api/src'),
  join(repoRoot, 'engine/src'),
  process.env.PYTHONPATH,
].filter(Boolean).join(delimiter);
const acceptanceRunId = process.env.DIAGRAM_ACCEPTANCE_RUN_ID;
const sourceFingerprint = process.env.DIAGRAM_ACCEPTANCE_SOURCE_FINGERPRINT;
if (!acceptanceRunId || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(acceptanceRunId)) {
  throw new Error('DIAGRAM_ACCEPTANCE_RUN_ID is required; use pnpm test:diagram-real');
}
if (!sourceFingerprint || !/^sha256:[0-9a-f]{64}$/.test(sourceFingerprint)) {
  throw new Error('DIAGRAM_ACCEPTANCE_SOURCE_FINGERPRINT is required; use pnpm test:diagram-real');
}

// This executes during Playwright collection. Registry growth without a
// fixture therefore fails before any model call and cannot become a skip.
const manifest = JSON.parse(execFileSync(python, [
  '-m', 'vibecanvas_api.diagrams.acceptance',
  'manifest', '--fixtures', fixturePath,
], {
  cwd: repoRoot,
  encoding: 'utf8',
  env: { ...process.env, PYTHONPATH: pythonPath },
})) as Manifest;

if (manifest.matrix.length !== manifest.fixtures.length * 2 * 2) {
  throw new Error('Diagram acceptance manifest is not a full type × runtime × stage matrix');
}

const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/([^/]+)\/chats\/([^/]+)\/messages$/;
const CREATE_REQUIRED_TOOLS = [
  'get_diagram_spec',
  'check_diagram',
  'review_diagram',
];
const MODIFY_REQUIRED_TOOLS = [
  'inspect_diagram',
  'check_diagram',
  'review_diagram',
];
// A successful render_interactive invocation is projected as an interactive
// artifact instead of a generic tool-call row. Its inline Diagram, closed side
// pane, click-to-open behavior, and full Preview are asserted separately below.
const IMAGE_REVIEW_TOOLS = new Set([
  'read_images',
  'view_image',
  'read_diagram_review_image',
]);
const MENTAL_MAP_ALIGNMENT_TOLERANCE_PX = 24;
const REALTIME_SCREEN_UPDATE_SLO_MS = 2_500;
// Protocol recovery belongs inside the single product Turn. A browser-level
// extra user message would hide a broken closed loop, so real acceptance gives
// every create/modify operation exactly one user Turn.
const MAX_ACCEPTANCE_RECOVERY_TURNS = 0;

test.setTimeout(1_200_000);

function normalized(value: string) {
  return value.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');
}

function fileEvidence(
  testInfo: TestInfo,
  absolutePath: string,
  revision: string | null,
): FileEvidence {
  const data = readFileSync(absolutePath);
  return {
    filename: relative(testInfo.outputDir, absolutePath),
    bytes: data.byteLength,
    sha256: `sha256:${createHash('sha256').update(data).digest('hex')}`,
    revision,
  };
}

async function captureFullDiagram(
  page: Page,
  preview: Locator,
  testInfo: TestInfo,
  absolutePath: string,
  stamp: string,
  revision: string,
): Promise<FileEvidence> {
  const fullscreen = preview.locator('[data-action="diagram-fullscreen"]');
  await fullscreen.click();
  await expect.poll(() => page.evaluate(() => Boolean(document.fullscreenElement)), {
    message: 'Diagram Preview entered real browser fullscreen',
  }).toBe(true);
  const minimap = preview.locator('.react-flow__minimap');
  if (await minimap.isVisible()) {
    await preview.locator('[data-action="diagram-toggle-minimap"]').click();
  }
  await preview.locator('[data-action="diagram-fit"]').click();
  await page.waitForTimeout(100);
  await preview.evaluate((element, label) => {
    const badge = document.createElement('div');
    badge.dataset.diagramEvidenceStamp = 'true';
    badge.textContent = label;
    Object.assign(badge.style, {
      position: 'absolute',
      left: '16px',
      bottom: '16px',
      zIndex: '100',
      padding: '8px 10px',
      borderRadius: '6px',
      background: 'rgba(15, 23, 42, 0.9)',
      color: '#ffffff',
      font: '600 12px/1.4 ui-monospace, monospace',
      pointerEvents: 'none',
    });
    element.appendChild(badge);
  }, `${stamp} · ${revision}`);
  await preview.screenshot({ path: absolutePath, animations: 'disabled' });
  await preview.locator('[data-diagram-evidence-stamp="true"]').evaluate((node) => node.remove());
  await page.evaluate(() => document.exitFullscreen());
  await expect.poll(() => page.evaluate(() => Boolean(document.fullscreenElement)))
    .toBe(false);
  return fileEvidence(testInfo, absolutePath, revision);
}

async function expectVisibleSceneEdges(preview: Locator, scene: Scene) {
  // The Preview deliberately virtualizes off-screen React Flow elements. A
  // preceding keyboard-search assertion may have focused one node, so first
  // exercise the real "fit diagram" control before evaluating the complete
  // compiled Scene.
  await preview.locator('[data-action="diagram-fit"]').click();
  const paths = preview.locator('.react-flow__edge-path');
  await expect(paths, 'every compiled Scene edge must be rendered in Preview')
    .toHaveCount(scene.edges.length);
  const rendered = await paths.evaluateAll((elements) => elements.map((element) => {
    const path = element as SVGPathElement;
    const style = getComputedStyle(path);
    return {
      length: path.getTotalLength(),
      stroke: style.stroke,
      strokeOpacity: Number.parseFloat(style.strokeOpacity),
      strokeWidth: Number.parseFloat(style.strokeWidth),
    };
  }));
  rendered.forEach((edge, index) => {
    // Playwright's generic visibility check treats a perfectly horizontal or
    // vertical SVG path as hidden because one bounding-box dimension is zero.
    // For stroked paths, non-zero path length plus a visible stroke is the
    // browser-level contract that corresponds to what the user actually sees.
    expect(edge.length, `Scene edge ${scene.edges[index].id} must have geometry`).toBeGreaterThan(0);
    expect(edge.stroke, `Scene edge ${scene.edges[index].id} must have a stroke`).not.toBe('none');
    expect(edge.strokeOpacity, `Scene edge ${scene.edges[index].id} stroke must be opaque`).toBeGreaterThan(0);
    expect(edge.strokeWidth, `Scene edge ${scene.edges[index].id} stroke must have width`).toBeGreaterThan(0);
  });

  const nodes = new Map(scene.nodes.map((node) => [node.id, node.bounds]));
  const touchesBoundary = (point: { x: number; y: number }, bounds: Bounds) => {
    const epsilon = 0.5;
    const insideX = point.x >= bounds.x - epsilon
      && point.x <= bounds.x + bounds.width + epsilon;
    const insideY = point.y >= bounds.y - epsilon
      && point.y <= bounds.y + bounds.height + epsilon;
    const onSide = Math.abs(point.x - bounds.x) <= epsilon
      || Math.abs(point.x - (bounds.x + bounds.width)) <= epsilon
      || Math.abs(point.y - bounds.y) <= epsilon
      || Math.abs(point.y - (bounds.y + bounds.height)) <= epsilon;
    return insideX && insideY && onSide;
  };
  for (const edge of scene.edges) {
    const source = nodes.get(edge.source);
    const target = nodes.get(edge.target);
    expect(source, `Scene edge ${edge.id} source node exists`).toBeTruthy();
    expect(target, `Scene edge ${edge.id} target node exists`).toBeTruthy();
    expect(edge.points.length, `Scene edge ${edge.id} has routed points`).toBeGreaterThanOrEqual(2);
    expect(
      touchesBoundary(edge.points[0], source!),
      `Scene edge ${edge.id} starts on source boundary`,
    ).toBe(true);
    expect(
      touchesBoundary(edge.points.at(-1)!, target!),
      `Scene edge ${edge.id} ends on target boundary`,
    ).toBe(true);
  }
}

function matchingNodes(scene: Scene, token: string): SceneNode[] {
  // Fixture tokens may list explicit, reviewable semantic aliases separated
  // by `|`. This keeps real-model wording variation out of the pass/fail
  // signal while every match still resolves to an actual scene node.
  const expectedAlternatives = token.split('|').map(normalized).filter(Boolean);
  const score = (node: SceneNode) => {
    const id = normalized(node.id);
    const label = normalized(node.label);
    return Math.min(...expectedAlternatives.map((expected) => {
      if (id === expected || label === expected) return 0;
      if (id.includes(expected)) return 1;
      if (label.startsWith(expected)) return 2;
      if (label.includes(expected)) return 3;
      if (expected.includes(label)) return 4;
      return Number.POSITIVE_INFINITY;
    }));
  };
  return scene.nodes
    .map((node) => ({ node, score: score(node) }))
    .filter((candidate) => Number.isFinite(candidate.score))
    .sort((left, right) => (
      left.score - right.score
      || normalized(left.node.label).length - normalized(right.node.label).length
      || left.node.id.localeCompare(right.node.id)
    ))
    .map((candidate) => candidate.node);
}

function matchingNode(scene: Scene, token: string): SceneNode | undefined {
  return matchingNodes(scene, token)[0];
}

function elementAssertions(scene: Scene, tokens: string[]) {
  return Object.fromEntries(tokens.map((token) => [
    `element:${token}`,
    Boolean(matchingNode(scene, token)),
  ]));
}

function mentalMapAssertions(before: Scene, after: Scene, tokens: string[]) {
  const pairs = tokens.map((token) => ({
    token,
    before: matchingNode(before, token),
    after: matchingNode(after, token),
  }));
  const xLimit = Math.max(320, before.bounds.width * 0.45);
  const yLimit = Math.max(180, before.bounds.height * 0.45);
  const boundedDisplacement = pairs.every((pair) => Boolean(
    pair.before && pair.after
    && Math.abs(pair.before.bounds.x - pair.after.bounds.x) <= xLimit
    && Math.abs(pair.before.bounds.y - pair.after.bounds.y) <= yLimit
  ));
  const relation = (left: SceneNode, right: SceneNode, axis: 'x' | 'y') => {
    const leftCenter = left.bounds[axis]
      + left.bounds[axis === 'x' ? 'width' : 'height'] / 2;
    const rightCenter = right.bounds[axis]
      + right.bounds[axis === 'x' ? 'width' : 'height'] / 2;
    const delta = leftCenter - rightCenter;
    return Math.abs(delta) <= MENTAL_MAP_ALIGNMENT_TOLERANCE_PX ? 0 : Math.sign(delta);
  };
  let relativeOrder = true;
  for (let leftIndex = 0; leftIndex < pairs.length; leftIndex += 1) {
    for (let rightIndex = leftIndex + 1; rightIndex < pairs.length; rightIndex += 1) {
      const left = pairs[leftIndex];
      const right = pairs[rightIndex];
      if (!left.before || !left.after || !right.before || !right.after) {
        relativeOrder = false;
        continue;
      }
      for (const axis of ['x', 'y'] as const) {
        const previous = relation(left.before, right.before, axis);
        const current = relation(left.after, right.after, axis);
        if (previous !== 0 && current !== 0 && previous !== current) {
          relativeOrder = false;
        }
      }
    }
  }
  return {
    'mental-map:bounded-displacement': boundedDisplacement,
    'mental-map:relative-order': relativeOrder,
  };
}

function modificationRecoveryIssues(
  before: Scene,
  after: Scene,
  assertions: Fixture['modify_assertions'],
) {
  const issues: string[] = [];
  for (const token of assertions.added_elements) {
    if (!matchingNode(after, token)) issues.push(`缺少新增节点「${token}」`);
  }
  for (const token of assertions.removed_or_replaced_elements) {
    if (matchingNode(after, token)) issues.push(`旧节点「${token}」仍存在，必须删除或替换`);
  }
  for (const token of assertions.preserved_elements) {
    const previous = matchingNode(before, token);
    const current = matchingNode(after, token);
    if (!current) {
      issues.push(`应保留的节点「${token}」已丢失`);
    } else if (assertions.preserve_stable_ids && previous?.id !== current.id) {
      issues.push(`节点「${token}」的 stable ID 从 ${previous?.id} 变成了 ${current.id}`);
    }
  }
  if (assertions.preserve_mental_map) {
    const mentalMap = mentalMapAssertions(before, after, assertions.preserved_elements);
    if (!mentalMap['mental-map:bounded-displacement']) {
      issues.push('保留节点移动过大，破坏了原图 mental map');
    }
    if (!mentalMap['mental-map:relative-order']) {
      issues.push('保留节点相对顺序改变，破坏了原图 mental map');
    }
  }
  return issues;
}

function relationAssertions(scene: Scene, relations: string[]) {
  return Object.fromEntries(relations.map((relation) => {
    const transitive = relation.includes('~>');
    const [sourceToken, targetToken] = relation.split(transitive ? '~>' : '->', 2);
    const sources = matchingNodes(scene, sourceToken);
    const targets = matchingNodes(scene, targetToken);
    const reachable = (sourceId: string, targetId: string) => {
      if (!transitive) {
        return scene.edges.some((edge) => (
          edge.source === sourceId && edge.target === targetId
        ));
      }
      const visited = new Set<string>([sourceId]);
      const queue = [sourceId];
      while (queue.length > 0) {
        const current = queue.shift()!;
        for (const edge of scene.edges) {
          if (edge.source !== current || visited.has(edge.target)) continue;
          if (edge.target === targetId) return true;
          visited.add(edge.target);
          queue.push(edge.target);
        }
      }
      return false;
    };
    return [
      `relation:${relation}`,
      sources.some((source) => (
        targets.some((target) => reachable(source.id, target.id))
      )),
    ];
  }));
}

function reviewOutcome(trace: Array<{
  name: string | null;
  status: string | null;
  text: string;
}>, recoveryTurns: number): ReviewOutcome {
  const reviews = trace.filter((entry) => (
    entry.name === 'review_diagram' && entry.status === 'done'
  ));
  if (!reviews.length) {
    return { accepted: false, mode: 'incomplete', review_count: 0, warnings: [] };
  }
  const reviewText = reviews.at(-1)!.text;
  // LangChain places the raw JSON immediately after "Output". Codex first
  // renders a tree-shaped tool card and appends the same raw, one-line JSON at
  // the end, so taking only the first line after the heading misclassifies a
  // real `next.action=deliver` as incomplete. Prefer the canonical reviewed
  // payload wherever it appears in the final tool card.
  const payloadLine = reviewText
    .split(/\r?\n/)
    .reverse()
    .map((line) => line.trim())
    .find((line) => line.startsWith('{"status": "reviewed"'))
    ?? reviewText.match(/(?:输出|Output)\n([^\n]+)/)?.[1];
  if (!payloadLine) {
    return {
      accepted: false,
      mode: 'incomplete',
      review_count: reviews.length,
      warnings: [],
    };
  }
  try {
    const payload = JSON.parse(payloadLine) as {
      visual_issues?: Array<{ severity?: string; code?: string }>;
      visual_metrics?: { overlap_count?: number; clipped_label_count?: number };
      image_delivery?: { delivered_to_model?: boolean };
      next?: { action?: string };
    };
    const issues = payload.visual_issues ?? [];
    const warnings = issues.map((issue) => issue.code ?? 'unknown_visual_warning');
    if (payload.next?.action === 'deliver') {
      return {
        accepted: true,
        mode: 'deliver',
        review_count: reviews.length,
        warnings,
      };
    }
    const boundedWarning = (
      recoveryTurns >= MAX_ACCEPTANCE_RECOVERY_TURNS
      && issues.length > 0
      && issues.length <= 1
      && issues.every((issue) => issue.severity === 'warning')
      && payload.visual_metrics?.overlap_count === 0
      && payload.visual_metrics?.clipped_label_count === 0
      && payload.image_delivery?.delivered_to_model === true
    );
    return {
      accepted: boundedWarning,
      mode: boundedWarning ? 'bounded_warning' : 'incomplete',
      review_count: reviews.length,
      warnings,
    };
  } catch {
    return {
      accepted: false,
      mode: 'incomplete',
      review_count: reviews.length,
      warnings: [],
    };
  }
}

function sceneIssuesAccepted(scene: Scene, outcome: ReviewOutcome) {
  const sceneCodes = scene.issues.map((issue) => issue.code).sort();
  const reviewCodes = [...outcome.warnings].sort();
  // `next.action=deliver` may explicitly disclose non-blocking warnings. The
  // Scene and final Review must agree exactly; requiring an empty Scene here
  // rejects a valid deliver response that faithfully reports its warning.
  if (outcome.mode === 'deliver') {
    return JSON.stringify(sceneCodes) === JSON.stringify(reviewCodes);
  }
  return outcome.mode === 'bounded_warning'
    && sceneCodes.length === 1
    && JSON.stringify(sceneCodes) === JSON.stringify(reviewCodes);
}

function missingRequiredTools(
  trace: Array<{ name: string | null; status: string | null }>,
  required: readonly string[],
) {
  return required.filter((name) => (
    !trace.some((entry) => entry.name === name && entry.status === 'done')
  ));
}

function hasCompletedImageReview(
  trace: Array<{ name: string | null; status: string | null }>,
) {
  return trace.some((entry) => (
    entry.name !== null
    && IMAGE_REVIEW_TOOLS.has(entry.name)
    && entry.status === 'done'
  ));
}

async function descriptorFor(
  session: E2ECookieSession,
  chatId: string,
  path: string,
): Promise<Descriptor> {
  return session.api('/api/v1/previews/resolve', {
    method: 'POST',
    body: JSON.stringify({
      fileRef: { schemaVersion: 1, scope: 'chat', chatId, path },
    }),
  }).then((response) => response.json()) as Promise<Descriptor>;
}

async function sourceFor(
  session: E2ECookieSession,
  chatId: string,
  path: string,
): Promise<Record<string, unknown>> {
  const workspace = await session.api(
    `/api/v1/chats/workspace?chat_id=${encodeURIComponent(chatId)}`,
  ).then((response) => response.json()) as { workspace_scope_id: string };
  const params = new URLSearchParams({
    wf_id: workspace.workspace_scope_id,
    path,
  });
  const payload = await session.api(`/api/v1/vfs/content?${params.toString()}`)
    .then((response) => response.json()) as { content: string };
  return JSON.parse(payload.content) as Record<string, unknown>;
}

async function timelineFor(
  page: Page,
  path: string,
  revision: string,
  options: { previewAlreadyMounted?: boolean } = {},
) {
  await expect.poll(async () => page.evaluate(({ expectedPath, expectedRevision }) => {
    const entries = window.__VIBECANVAS_DIAGRAM_TIMELINE__ ?? [];
    return ['T0', 'T1', 'T2', 'T3'].every((stage) => entries.some((entry) => (
      entry.stage === stage
      && entry.path === expectedPath
      && entry.revision === expectedRevision
    )));
  }, { expectedPath: path, expectedRevision: revision }), {
    timeout: 30_000,
    message: `T0–T3 timeline for ${path} ${revision}`,
  }).toBe(true);
  const entries = await page.evaluate(({ expectedPath, expectedRevision }) => (
    (window.__VIBECANVAS_DIAGRAM_TIMELINE__ ?? []).filter((entry) => (
      entry.path === expectedPath && entry.revision === expectedRevision
    ))
  ), { expectedPath: path, expectedRevision: revision });
  const timestamp = (stage: string) => entries
    .filter((entry) => entry.stage === stage)
    .at(0)?.timestamp ?? Number.NaN;
  const result = {
    revision,
    T0: timestamp('T0'),
    T1: timestamp('T1'),
    T2: timestamp('T2'),
    T3: timestamp('T3'),
  };
  expect(result.T0).toBeLessThanOrEqual(result.T1);
  // The Preview can receive the same committed revision through the active
  // Chat descriptor before the dedicated preview SSE observer records T1.
  // Both delivery paths are required, but their browser callbacks may race.
  expect(result.T0).toBeLessThanOrEqual(result.T2);
  expect(result.T2).toBeLessThanOrEqual(result.T3);
  expect(result.T3 - result.T2).toBeLessThanOrEqual(2000);
  if (options.previewAlreadyMounted) {
    expect(result.T1 - result.T0).toBeLessThanOrEqual(2000);
    expect(Math.max(result.T1, result.T3) - result.T0)
      .toBeLessThanOrEqual(REALTIME_SCREEN_UPDATE_SLO_MS);
  }
  return result;
}

async function toolTrace(page: Page, start: number) {
  const activities = page.locator('[data-tool-activity="true"]');
  for (let index = 0; index < await activities.count(); index += 1) {
    const toggle = activities.nth(index).locator('[data-action="tool-activity-toggle"]');
    if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  }
  const calls = page.locator('[data-role="tool-call"]');
  const total = await calls.count();
  const trace = [];
  for (let index = start; index < total; index += 1) {
    const call = calls.nth(index);
    const toggle = call.locator('[data-action="tool-call-toggle"]');
    if (await toggle.count() && await toggle.getAttribute('aria-expanded') !== 'true') {
      await toggle.click();
    }
    trace.push({
      name: await call.getAttribute('data-tool-name'),
      status: await call.getAttribute('data-tool-status'),
      // Review payloads include structured geometry and can exceed 8 KiB for
      // real architecture diagrams. Keep enough text to preserve the final
      // canonical JSON instead of misclassifying a complete Review as missing.
      text: (await call.innerText()).slice(0, 64_000),
    });
  }
  return trace;
}

async function sendAndWait(
  page: Page,
  prompt: string,
  options: { expectStartupProgress?: boolean } = {},
): Promise<{ chatId: string; reply: string; startupProgressLatencyMs: number | null }> {
  const composer = page.locator('[data-role="agent-composer-input"]');
  const completedAnswers = page.locator('[data-message-role="assistant"]')
    .filter({ has: page.locator('[data-role="markdown"]') });
  const answerCountBefore = await completedAnswers.count();
  await composer.fill(prompt);
  const sentAt = Date.now();
  const startupProgress = page.locator('[data-role="agent-startup-phase"]').last();
  const responsePromise = page.waitForResponse((candidate) => (
    candidate.request().method() === 'POST'
    && MESSAGE_PATH.test(new URL(candidate.url()).pathname)
  ), { timeout: 30_000 });
  const startupProgressPromise = options.expectStartupProgress
    ? expect(startupProgress).toBeVisible({ timeout: 5_000 })
      .then(() => Date.now() - sentAt)
    : Promise.resolve(null);
  await Promise.all([
    responsePromise,
    page.locator('[data-action="agent-composer-send"]').click(),
  ]);
  const response = await responsePromise;
  // Do not let a slower POST response inflate the UI visibility metric: the
  // startup phase is streamed independently and is the user-facing boundary.
  const startupProgressLatencyMs = await startupProgressPromise;
  expect(response.ok()).toBe(true);
  const match = new URL(response.url()).pathname.match(MESSAGE_PATH);
  expect(match).not.toBeNull();
  await expect.poll(() => completedAnswers.count(), {
    timeout: 600_000,
    message: 'a new completed assistant answer',
  }).toBeGreaterThan(answerCountBefore);
  await expect(composer).toBeEnabled({ timeout: 600_000 });
  const answer = completedAnswers.last();
  await expect(answer).toBeVisible();
  return {
    chatId: match![2],
    reply: await answer.innerText(),
    startupProgressLatencyMs,
  };
}

async function openNewChat(
  page: Page,
  runtime: RuntimeName,
  accountModelOption: string | null,
) {
  await page.goto('/chat', { timeout: 30_000, waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
    timeout: 30_000,
  });
  await page.locator('[data-action="chat-new"]').click();
  await expect(page.locator('[data-message-role="user"]')).toHaveCount(0, {
    timeout: 30_000,
  });
  await expect(page.locator('[data-message-role="assistant"]')).toHaveCount(0, {
    timeout: 30_000,
  });
  if (runtime === 'codex' && accountModelOption) {
    await expect(page.locator('[data-role="chat-model-select"]')).toBeEnabled({
      timeout: 30_000,
    });
    await page.locator('[data-role="chat-model-select"]').click();
    await page.getByRole('option', { name: accountModelOption, exact: true }).click();
  }
  await page.locator('[data-role="chat-composer-options-toggle"]').click();
  await expect(page.locator('[data-role="chat-approval-mode-select"]')).toHaveCount(0);
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeEnabled({
    timeout: 120_000,
  });
  return (await page.locator('[data-role="chat-model-select"]').innerText()).trim();
}

for (const runtime of ['langchain', 'codex'] as const satisfies readonly RuntimeName[]) {
  test.describe(`${runtime} real Agent Diagram matrix`, () => {
    const session = new E2ECookieSession();
    const accountRoots: string[] = [];
    let codexModelOption: string | null = null;
    let configuredRuntime: RuntimeName | null = null;

    test.beforeAll(async () => {
      await session.register(`diagram-acceptance-${runtime}`);
      const settingsResponse = await session.api('/api/v1/agent-runtime/settings', {
        method: 'PUT',
        body: JSON.stringify({ default_runtime_type: runtime }),
      });
      expect(settingsResponse.ok).toBeTruthy();
      const settings = await settingsResponse.json() as { default_runtime_type: RuntimeName };
      expect(settings.default_runtime_type).toBe(runtime);
      configuredRuntime = settings.default_runtime_type;
      if (runtime !== 'codex') return;
      if (CODEX_ACCEPTANCE_AUTH === 'account') {
        const source = join(homedir(), '.codex', 'auth.json');
        if (!existsSync(source)) throw new Error(`host Codex identity is missing: ${source}`);
        const me = await session.api('/api/v1/auth/me').then((response) => response.json()) as {
          tenant_id: string;
          user_id: string;
        };
        const runtimeRoot = resolve(
          process.env.AGENT_RUNTIME_ROOT ?? join(homedir(), '.vibecanvas', 'agent-runtime'),
        );
        const accountRoot = resolve(runtimeRoot, me.tenant_id, me.user_id, 'codex-account-v1');
        if (!accountRoot.startsWith(`${runtimeRoot}${sep}`)) {
          throw new Error('refusing to create Codex identity outside AGENT_RUNTIME_ROOT');
        }
        const accountHome = join(accountRoot, '.codex');
        mkdirSync(accountHome, { recursive: true, mode: 0o700 });
        chmodSync(accountHome, 0o700);
        copyFileSync(source, join(accountHome, 'auth.json'));
        chmodSync(join(accountHome, 'auth.json'), 0o600);
        accountRoots.push(accountRoot);
      }
      const capabilities = await session.api('/api/v1/agent-runtime/capabilities')
        .then((response) => response.json()) as {
          authenticated: boolean | null;
          default_model_id: string | null;
          models: Array<{ id: string; label: string; provider?: string }>;
        };
      expect(capabilities.authenticated).toBe(true);
      if (CODEX_ACCEPTANCE_AUTH === 'account') {
        expect(capabilities.models.some((item) => item.provider === 'chatgpt')).toBe(true);
        // The pre-Chat catalog can include models from other configured Codex
        // auth methods. Once the Chat is immutably bound to account mode, its
        // model catalog is account-specific; use that Runtime's own default.
        codexModelOption = null;
        return;
      }
      const model = capabilities.models.find((item) => item.provider !== 'chatgpt')
        ?? capabilities.models[0];
      if (!model) throw new Error('Codex exposes no configured model');
      codexModelOption = capabilities.default_model_id === model.id
        ? null
        : `${model.label}${model.provider ? ` (${model.provider})` : ''}`;
    });

    test.afterAll(() => {
      for (const accountRoot of accountRoots) {
        const runtimeRoot = resolve(
          process.env.AGENT_RUNTIME_ROOT ?? join(homedir(), '.vibecanvas', 'agent-runtime'),
        );
        if (resolve(accountRoot).startsWith(`${runtimeRoot}${sep}`)) {
          rmSync(accountRoot, { recursive: true, force: true });
        }
      }
    });

    test.beforeEach(async ({ context }: { context: BrowserContext }) => {
      await session.seed(context, 'zh');
    });

    for (const fixture of manifest.fixtures) {
      test(`${fixture.family}/${fixture.type} create + modify`, async ({ page }, testInfo) => {
        page.setDefaultTimeout(30_000);
        await page.setViewportSize({ width: 1920, height: 1080 });
        await page.emulateMedia({ reducedMotion: 'reduce' });
        const consoleErrors: string[] = [];
        const networkErrors: string[] = [];
        page.on('pageerror', (error) => consoleErrors.push(error.message));
        page.on('console', (message) => {
          if (message.type() !== 'error') return;
          const location = message.location().url;
          consoleErrors.push(location ? `${message.text()} (${location})` : message.text());
        });
        page.on('requestfailed', (request) => {
          const errorText = request.failure()?.errorText ?? 'unknown failure';
          // Reload/navigation intentionally cancels obsolete document and SSE
          // requests. Those are browser lifecycle events, not product failures.
          if (!/ERR_ABORTED|NS_BINDING_ABORTED/i.test(errorText)) {
            networkErrors.push(`${request.method()} ${request.url()} ${errorText}`);
          }
        });
        page.on('response', (response) => {
          const resourceType = response.request().resourceType();
          const failedBrowserResource = response.status() >= 400
            && ['document', 'stylesheet', 'script', 'image', 'font'].includes(resourceType);
          if (response.status() >= 500 || failedBrowserResource) {
            networkErrors.push(`${response.status()} ${response.url()}`);
          }
        });

        const screenshotPaths = {
          before: testInfo.outputPath('before.png'),
          create_light_full: testInfo.outputPath('create-light-full.png'),
          modify_light_full: testInfo.outputPath('modify-light-full.png'),
          refresh_light_full: testInfo.outputPath('refresh-light-full.png'),
          modify_dark_full: testInfo.outputPath('modify-dark-full.png'),
        };
        const screenshotEvidence: Record<string, FileEvidence> = {};
        const modelLabel = await openNewChat(page, runtime, codexModelOption);
        expect(configuredRuntime).toBe(runtime);
        expect(modelLabel.length).toBeGreaterThan(0);
        await page.screenshot({ path: screenshotPaths.before, fullPage: true });
        screenshotEvidence.before = fileEvidence(testInfo, screenshotPaths.before, null);

        const createPrompt = `${fixture.create_prompt} 完成并实际查看 review 图片后，`
          + `${fixture.visual_question}`;
        const createCallStart = await page.locator('[data-role="tool-call"]').count();
        let createResult = await sendAndWait(page, createPrompt, {
          expectStartupProgress: true,
        });
        const chatId = createResult.chatId;
        // Capture and attach the complete tool output before Preview assertions.
        // If presentation fails, the first-failure artifact still contains the
        // structured MCP error instead of only a missing-element timeout.
        let createTrace = await toolTrace(page, createCallStart);
        let createRecoveryTurns = 0;
        while (
          createRecoveryTurns < MAX_ACCEPTANCE_RECOVERY_TURNS
          && (
            missingRequiredTools(createTrace, CREATE_REQUIRED_TOOLS).length > 0
            || !reviewOutcome(createTrace, createRecoveryTurns).accepted
            || !hasCompletedImageReview(createTrace)
          )
        ) {
          createRecoveryTurns += 1;
          const incompleteCreate = missingRequiredTools(createTrace, CREATE_REQUIRED_TOOLS);
          createResult = await sendAndWait(
            page,
            `继续完成当前 Diagram 的创建验收。你尚未完成：${[
              ...incompleteCreate,
              ...(!reviewOutcome(createTrace, createRecoveryTurns).accepted
                ? ['final review deliver'] : []),
              ...(!hasCompletedImageReview(createTrace)
                ? ['用当前 Runtime 的读图工具打开最新 review_images.sandbox_path，并回答图像问题']
                : []),
            ].join(', ')}。不要创建新图；如果 review 返回 next.action=call_tool，必须按其 repair_element_ids 和 then_workflow 实际修改后再 review，不能对未变化的 revision 重复 review。实际查看最终 review 图片后，${fixture.visual_question}`,
          );
          expect(createResult.chatId).toBe(chatId);
          createTrace = await toolTrace(page, createCallStart);
        }
        const createTracePath = testInfo.outputPath('create-tool-trace.json');
        writeFileSync(createTracePath, JSON.stringify(createTrace, null, 2));
        await testInfo.attach('create-tool-trace', {
          path: createTracePath,
          contentType: 'application/json',
        });
        expect(
          missingRequiredTools(createTrace, CREATE_REQUIRED_TOOLS),
          'missing required create tools',
        ).toEqual([]);
        expect(
          hasCompletedImageReview(createTrace),
          'creation must actually read the final Review image',
        ).toBe(true);
        const createReviewOutcome = reviewOutcome(createTrace, createRecoveryTurns);
        expect(createReviewOutcome.accepted, 'final create review must be accepted').toBe(true);
        const createCard = page.locator(
          '[data-role="interactive-artifact"][data-tool-name="render_interactive"]',
        ).last();
        await expect(createCard).toBeVisible({ timeout: 60_000 });
        await expect(createCard.locator('.diagram-inline-preview')).toBeVisible();
        await expect(page.locator('[data-role="chat-preview-pane"]')).toHaveCount(0);
        await createCard.locator('[data-action="interactive-open-file-preview"]').click();
        const preview = page.locator(
          '[data-role="chat-preview-pane"] [data-role="diagram-preview"]',
        );
        await expect(preview).toBeVisible({ timeout: 60_000 });
        await expect(preview).toHaveAttribute('data-reduced-motion', 'true');
        await expect(preview.getByRole('toolbar', { name: /Diagram controls|图/ }))
          .toBeVisible();
        await expect(preview).toHaveAttribute('data-diagram-revision', /.+/);
        const path = await preview.getAttribute('data-diagram-path');
        const createRevision = await preview.getAttribute('data-diagram-revision');
        expect(path).toMatch(/^\/data\/diagrams\/.+\.vdiagram\.json$/);
        expect(createRevision).toBeTruthy();
        const createTimeline = await timelineFor(page, path!, createRevision!);
        const createDescriptor = await descriptorFor(session, chatId, path!);
        writeFileSync(
          testInfo.outputPath('create-scene.json'),
          JSON.stringify(createDescriptor.diagram.scene, null, 2),
        );
        expect(createDescriptor.revision).toBe(createRevision);
        expect(createDescriptor.diagram.scene.family).toBe(fixture.family);
        expect(createDescriptor.diagram.scene.diagramType).toBe(fixture.type);
        await expectVisibleSceneEdges(preview, createDescriptor.diagram.scene);
        expect(
          sceneIssuesAccepted(createDescriptor.diagram.scene, createReviewOutcome),
          'create Scene warnings must match the accepted Review outcome',
        ).toBe(true);
        const createSource = await sourceFor(session, chatId, path!);
        writeFileSync(
          testInfo.outputPath('create-source.json'),
          JSON.stringify(createSource, null, 2),
        );
        const createAssertions = {
          ...elementAssertions(
            createDescriptor.diagram.scene,
            fixture.create_assertions.required_elements,
          ),
          ...relationAssertions(
            createDescriptor.diagram.scene,
            fixture.create_assertions.required_relations,
          ),
        };
        createAssertions['visual-review-accepted'] = sceneIssuesAccepted(
          createDescriptor.diagram.scene,
          createReviewOutcome,
        );
        expect(
          Object.entries(createAssertions).filter(([, passed]) => !passed),
        ).toEqual([]);
        const leftmost = Math.min(...createDescriptor.diagram.scene.nodes.map(
          (node) => node.bounds.x,
        ));
        const leftmostLabels = createDescriptor.diagram.scene.nodes
          .filter((node) => node.bounds.x === leftmost)
          .map((node) => node.label);
        const visualAnswerCorrect = leftmostLabels.some((label) => (
          normalized(createResult.reply).includes(normalized(label))
        ));
        createAssertions['review-image-answer'] = visualAnswerCorrect;
        expect(visualAnswerCorrect, JSON.stringify({
          reply: createResult.reply,
          expectedLeftmostLabels: leftmostLabels,
        })).toBe(true);
        createAssertions['final-review-accepted'] = createReviewOutcome.accepted;
        screenshotEvidence.create_light_full = await captureFullDiagram(
          page,
          preview,
          testInfo,
          screenshotPaths.create_light_full,
          `${runtime} · ${fixture.family}/${fixture.type} · create · light`,
          createRevision!,
        );

        const selectedBefore = await test.step('select a preserved node with keyboard search', async () => {
          const selected = matchingNode(
            createDescriptor.diagram.scene,
            fixture.modify_assertions.preserved_elements[0],
          );
          expect(selected).toBeTruthy();
          // This validates a real keyboard interaction and remains reliable
          // when the minimap HUD overlaps a node after Fit.
          const nodeSearch = page.getByRole('textbox', { name: /Find a node|查找节点/ });
          await nodeSearch.fill(selected!.label);
          await nodeSearch.press('Enter');
          await expect(preview.locator(`[data-diagram-element-id="${selected!.id}"]`))
            .toHaveClass(/ring-2/);
          await nodeSearch.fill('');
          return selected!;
        });
        const viewportBefore = await preview.locator('.react-flow__viewport').getAttribute('style');
        await page.locator('[data-action="chat-preview-toggle"]').click();
        await expect(page.locator('[data-role="chat-preview-pane"]')).toHaveCount(0);

        const modifyPrompt = `${fixture.modify_prompt} 修改完成并实际查看更新后的 review 图片后，`
          + `${fixture.visual_question}`;
        const modifyCallStart = await page.locator('[data-role="tool-call"]').count();
        let modifyResult = await test.step(
          'send the natural-language modification and wait for completion',
          () => sendAndWait(page, modifyPrompt),
        );
        expect(modifyResult.chatId).toBe(chatId);
        let modifyTrace = await toolTrace(page, modifyCallStart);
        let modifySemanticIssues = modificationRecoveryIssues(
          createDescriptor.diagram.scene,
          (await descriptorFor(session, chatId, path!)).diagram.scene,
          fixture.modify_assertions,
        );
        let modifyRecoveryTurns = 0;
        while (
          modifyRecoveryTurns < MAX_ACCEPTANCE_RECOVERY_TURNS
          && (
            missingRequiredTools(modifyTrace, MODIFY_REQUIRED_TOOLS).length > 0
            || !reviewOutcome(modifyTrace, modifyRecoveryTurns).accepted
            || !hasCompletedImageReview(modifyTrace)
            || modifySemanticIssues.length > 0
          )
        ) {
          modifyRecoveryTurns += 1;
          const incompleteModify = missingRequiredTools(modifyTrace, MODIFY_REQUIRED_TOOLS);
          modifyResult = await sendAndWait(
            page,
            `继续完成当前 Diagram 的修改验收。你尚未完成：${[
              ...incompleteModify,
              ...(!reviewOutcome(modifyTrace, modifyRecoveryTurns).accepted
                ? ['final review deliver'] : []),
              ...(!hasCompletedImageReview(modifyTrace)
                ? ['用当前 Runtime 的读图工具打开最新 review_images.sandbox_path，并回答图像问题']
                : []),
              ...modifySemanticIssues.map((issue) => `语义修复：${issue}`),
            ].join(', ')}。不要创建新图；如果 review 返回 next.action=call_tool，必须按其 repair_element_ids 和 then_workflow 实际修改后再 review，不能对未变化的 revision 重复 review。基于当前 revision 完成缺失步骤，实际查看最终 review 图片后，${fixture.visual_question}`,
          );
          expect(modifyResult.chatId).toBe(chatId);
          modifyTrace = await toolTrace(page, modifyCallStart);
          modifySemanticIssues = modificationRecoveryIssues(
            createDescriptor.diagram.scene,
            (await descriptorFor(session, chatId, path!)).diagram.scene,
            fixture.modify_assertions,
          );
        }
        const modifyTracePath = testInfo.outputPath('modify-tool-trace.json');
        writeFileSync(modifyTracePath, JSON.stringify(modifyTrace, null, 2));
        await testInfo.attach('modify-tool-trace', {
          path: modifyTracePath,
          contentType: 'application/json',
        });
        expect(
          missingRequiredTools(modifyTrace, MODIFY_REQUIRED_TOOLS),
          'missing required modify tools',
        ).toEqual([]);
        expect(
          hasCompletedImageReview(modifyTrace),
          'modification must actually read the final Review image',
        ).toBe(true);
        expect(modifySemanticIssues, 'unresolved semantic modification issues').toEqual([]);
        const modifyReviewOutcome = reviewOutcome(modifyTrace, modifyRecoveryTurns);
        expect(modifyReviewOutcome.accepted, 'final modify review must be accepted').toBe(true);
        await expect(page.locator('[data-role="chat-preview-pane"]')).toHaveCount(0);
        const modifyCard = page.locator(
          '[data-role="interactive-artifact"][data-tool-name="render_interactive"]',
        ).last();
        await expect(modifyCard.locator('.diagram-inline-preview')).toBeVisible();
        await modifyCard.locator('[data-action="interactive-open-file-preview"]').click();
        await expect(preview).toBeVisible();
        await expect(preview).not.toHaveAttribute('data-diagram-revision', createRevision!);
        const modifyPath = await preview.getAttribute('data-diagram-path');
        expect(
          modifyPath,
          'a Diagram modification must update the existing artifact in place',
        ).toBe(path);
        const modifyRevision = await preview.getAttribute('data-diagram-revision');
        expect(modifyRevision).toBeTruthy();
        const modifyTimeline = await timelineFor(
          page,
          path!,
          modifyRevision!,
          { previewAlreadyMounted: true },
        );
        const modifyDescriptor = await descriptorFor(session, chatId, path!);
        writeFileSync(
          testInfo.outputPath('modify-scene.json'),
          JSON.stringify(modifyDescriptor.diagram.scene, null, 2),
        );
        expect(modifyDescriptor.revision).toBe(modifyRevision);
        expect(
          sceneIssuesAccepted(modifyDescriptor.diagram.scene, modifyReviewOutcome),
          'modify Scene warnings must match the accepted Review outcome',
        ).toBe(true);
        const modifySource = await sourceFor(session, chatId, path!);
        writeFileSync(
          testInfo.outputPath('modify-source.json'),
          JSON.stringify(modifySource, null, 2),
        );
        const modifyAssertions: Record<string, boolean> = {
          ...elementAssertions(
            modifyDescriptor.diagram.scene,
            fixture.modify_assertions.added_elements,
          ),
          ...Object.fromEntries(fixture.modify_assertions.removed_or_replaced_elements.map(
            (token) => [`removed:${token}`, !matchingNode(modifyDescriptor.diagram.scene, token)],
          )),
          ...elementAssertions(
            modifyDescriptor.diagram.scene,
            fixture.modify_assertions.preserved_elements,
          ),
        };
        modifyAssertions['visual-review-accepted'] = sceneIssuesAccepted(
          modifyDescriptor.diagram.scene,
          modifyReviewOutcome,
        );
        for (const token of fixture.modify_assertions.preserved_elements) {
          const before = matchingNode(createDescriptor.diagram.scene, token);
          const after = matchingNode(modifyDescriptor.diagram.scene, token);
          modifyAssertions[`stable-id:${token}`] = Boolean(before && after && before.id === after.id);
        }
        Object.assign(
          modifyAssertions,
          mentalMapAssertions(
            createDescriptor.diagram.scene,
            modifyDescriptor.diagram.scene,
            fixture.modify_assertions.preserved_elements,
          ),
        );
        const modifyLeftmost = Math.min(...modifyDescriptor.diagram.scene.nodes.map(
          (node) => node.bounds.x,
        ));
        const modifyLeftmostLabels = modifyDescriptor.diagram.scene.nodes
          .filter((node) => node.bounds.x === modifyLeftmost)
          .map((node) => node.label);
        modifyAssertions['review-image-answer'] = modifyLeftmostLabels.some((label) => (
          normalized(modifyResult.reply).includes(normalized(label))
        ));
        expect(modifyAssertions['review-image-answer'], JSON.stringify({
          reply: modifyResult.reply,
          expectedLeftmostLabels: modifyLeftmostLabels,
        })).toBe(true);
        expect(
          Object.entries(modifyAssertions).filter(([, passed]) => !passed),
        ).toEqual([]);
        expect(await preview.locator('.react-flow__viewport').getAttribute('style')).toBe(viewportBefore);
        await expect(preview.locator(`[data-diagram-element-id="${selectedBefore!.id}"]`))
          .toHaveClass(/ring-2/);
        await expectVisibleSceneEdges(preview, modifyDescriptor.diagram.scene);
        modifyAssertions['final-review-accepted'] = modifyReviewOutcome.accepted;
        await preview.locator('.react-flow__pane').click({ position: { x: 12, y: 12 } });
        screenshotEvidence.modify_light_full = await captureFullDiagram(
          page,
          preview,
          testInfo,
          screenshotPaths.modify_light_full,
          `${runtime} · ${fixture.family}/${fixture.type} · modify · light`,
          modifyRevision!,
        );
        const evidenceViewport = await preview.locator('.react-flow__viewport').getAttribute('style');

        const exportEvidence: Record<string, ExportEvidence> = {};

        await page.reload({ waitUntil: 'domcontentloaded' });
        await expect(preview).toHaveAttribute(
          'data-diagram-revision',
          modifyRevision!,
          { timeout: 60_000 },
        );
        expect(await preview.locator('.react-flow__viewport').getAttribute('style')).toBe(evidenceViewport);
        await expectVisibleSceneEdges(
          preview,
          modifyDescriptor.diagram.scene,
        );
        screenshotEvidence.refresh_light_full = await captureFullDiagram(
          page,
          preview,
          testInfo,
          screenshotPaths.refresh_light_full,
          `${runtime} · ${fixture.family}/${fixture.type} · refresh · light`,
          modifyRevision!,
        );

        await test.step('verify Dark Preview and theme-independent Light exports', async () => {
          await page.evaluate(() => localStorage.setItem('theme', 'dark'));
          await page.reload({ waitUntil: 'domcontentloaded' });
          const darkPreview = preview;
          await expect(darkPreview).toHaveAttribute('data-diagram-theme', 'dark');
          await expect(darkPreview).toHaveAttribute(
            'data-diagram-revision',
            modifyRevision!,
            { timeout: 60_000 },
          );
          await expect(darkPreview.locator('[data-diagram-element-id]').first())
            .toHaveCSS('border-color', 'rgb(85, 93, 104)');
          screenshotEvidence.modify_dark_full = await captureFullDiagram(
            page,
            darkPreview,
            testInfo,
            screenshotPaths.modify_dark_full,
            `${runtime} · ${fixture.family}/${fixture.type} · modify · dark`,
            modifyRevision!,
          );

          // Export is intentionally theme-independent: even from a Dark
          // Preview every downloadable artifact is the standard Light/white
          // representation. Persist exact bytes for release verification.
          const exportDir = testInfo.outputPath('exports');
          mkdirSync(exportDir, { recursive: true });
          for (const format of ['SVG', 'PNG', 'PDF'] as const) {
            await darkPreview.locator('[data-action="diagram-export"]').click();
            const [download] = await Promise.all([
              page.waitForEvent('download'),
              page.getByRole('menuitem', { name: new RegExp(`^${format}`) }).click(),
            ]);
            const filename = download.suggestedFilename();
            const key = format.toLocaleLowerCase() as 'svg' | 'png' | 'pdf';
            expect(filename.toLocaleLowerCase()).toMatch(new RegExp(`\\.${key}$`));
            const savedPath = join(exportDir, filename);
            await download.saveAs(savedPath);
            expect(statSync(savedPath).size).toBeGreaterThan(0);
            if (key === 'svg') {
              const svg = readFileSync(savedPath, 'utf8');
              expect(svg).toContain('fill="#ffffff"');
              expect(svg).not.toContain('#17191d');
            }
            exportEvidence[key] = {
              ...fileEvidence(testInfo, savedPath, modifyRevision!),
              format: key,
              theme: 'light',
              background: 'white',
            };
          }
        });

        const metadata = createSource.metadata as Record<string, string>;
        const evidence = {
          schema_version: 2,
          acceptance_run_id: acceptanceRunId,
          source_fingerprint: sourceFingerprint,
          fixture_id: fixture.id,
          family: fixture.family,
          type: fixture.type,
          runtime,
          runtime_evidence: {
            configured_runtime: configuredRuntime,
            model_label: modelLabel,
          },
          recovery_turns: {
            create: createRecoveryTurns,
            modify: modifyRecoveryTurns,
          },
          review_outcomes: {
            create: createReviewOutcome,
            modify: modifyReviewOutcome,
          },
          chat_id: chatId,
          command_catalog_version: manifest.registry_version,
          spec_hash: metadata.specHash,
          prompts: { create: createPrompt, modify: modifyPrompt },
          agent_replies: { create: createResult.reply, modify: modifyResult.reply },
          tool_trace: [...createTrace, ...modifyTrace],
          semantic_diff: {
            create_revision: createRevision,
            modify_revision: modifyRevision,
            preserved_ids: fixture.modify_assertions.preserved_elements.map((token) => ({
              token,
              before: matchingNode(createDescriptor.diagram.scene, token)?.id,
              after: matchingNode(modifyDescriptor.diagram.scene, token)?.id,
            })),
          },
          stages: {
            create: {
              status: 'pass', revision: createRevision, source_path: path,
              timeline: createTimeline, assertions: createAssertions,
              tool_names: createTrace.map((entry) => entry.name).filter(Boolean),
            },
            modify: {
              status: 'pass', revision: modifyRevision, source_path: path,
              timeline: modifyTimeline, assertions: modifyAssertions,
              tool_names: modifyTrace.map((entry) => entry.name).filter(Boolean),
            },
          },
          screenshots: screenshotEvidence,
          model_image_answers: {
            create: createResult.reply,
            modify: modifyResult.reply,
          },
          accessibility: {
            reduced_motion: await preview.getAttribute('data-reduced-motion') === 'true',
            toolbar_named: await preview.getByRole('toolbar').count() === 1,
            semantic_summary: await preview.locator('[data-role="diagram-accessible-summary"]')
              .innerText(),
            keyboard_selection: true,
          },
          startup_progress: {
            first_visible_ms: createResult.startupProgressLatencyMs,
          },
          exports: exportEvidence,
          console_errors: consoleErrors,
          network_errors: networkErrors,
          first_failure_trace: null,
        };
        expect(consoleErrors).toEqual([]);
        expect(networkErrors).toEqual([]);
        const evidencePath = testInfo.outputPath('evidence.json');
        writeFileSync(evidencePath, JSON.stringify(evidence, null, 2));
        await testInfo.attach('diagram-acceptance-evidence', {
          path: evidencePath,
          contentType: 'application/json',
        });

        const bootstrap = await session.api('/api/v1/chats/bootstrap', {}, true);
        if (bootstrap.ok) {
          const body = await bootstrap.json() as { carrier_scope_id: string };
          await session.api(
            `/api/v1/chat-scopes/${encodeURIComponent(body.carrier_scope_id)}`
              + `/chats/${encodeURIComponent(chatId)}`,
            { method: 'DELETE' },
            true,
          );
        }
      });
    }
  });
}
