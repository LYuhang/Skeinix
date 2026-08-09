/**
 * NodeHoverCard is a conservative node preview card.
 *
 * These tests drive NodeHoverCard DIRECTLY (it has no @xyflow/react dependency,
 * so it needs no xyflow mock — keeping it a safe sibling under vitest
 * `isolate:false`, which would otherwise clobber the feedback file's hoisted
 * @xyflow/react mock; see feedback_vitest_isolate_false). jsdom can't reproduce
 * the 500ms hover-delay timing, so we pass `forceOpen` to inspect the rendered
 * content; the suppress / no-empty-card RULES still force the card SHUT
 * regardless of `forceOpen`, which is exactly what we assert.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { render, cleanup } from '@testing-library/react';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';
import en from '@/lib/i18n/locales/en.json';
import {
  NodeHoverCard,
} from '@/pages/canvas/nodes/NodeHoverCard';
import { hasNodeHoverContent } from '@/pages/canvas/nodes/node-hover-utils';
import type { NodeExecState } from '@/pages/canvas/nodes/CustomNode';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: en } },
  interpolation: { escapeValue: false },
});

interface Overrides {
  description?: string;
  execState?: NodeExecState;
  execResult?: string;
  execError?: string;
  warnings?: string[];
  inputCount?: number;
  outputCount?: number;
  suppressed?: boolean;
  forceOpen?: boolean;
}

function renderCard(o: Overrides = {}) {
  return render(
    <I18nextProvider i18n={testI18n}>
      <NodeHoverCard
        title="my node"
        typeLabel="Code"
        description={o.description}
        execState={o.execState ?? null}
        execResult={o.execResult}
        execError={o.execError}
        warnings={o.warnings ?? []}
        inputCount={o.inputCount}
        outputCount={o.outputCount}
        suppressed={o.suppressed ?? false}
        forceOpen={o.forceOpen ?? true}
      >
        <div data-testid="node-body">body</div>
      </NodeHoverCard>
    </I18nextProvider>,
  );
}

const card = () => document.querySelector('[data-node-hover-card]');

describe('NodeHoverCard — content by priority', () => {
  afterEach(() => cleanup());

  it('always renders the trigger children', () => {
    renderCard({ description: 'hi' });
    expect(document.querySelector('[data-testid="node-body"]')).not.toBeNull();
  });

  it('shows name + type + description when a description exists', () => {
    renderCard({ description: 'does a thing' });
    expect(card()).not.toBeNull();
    expect(card()!.textContent).toContain('my node');
    expect(card()!.textContent).toContain('Code');
    expect(
      document.querySelector('[data-hover-description]')!.textContent,
    ).toContain('does a thing');
  });

  it('shows the running exec line when this node is running', () => {
    renderCard({ execState: 'running' });
    expect(
      document.querySelector('[data-hover-exec="running"]'),
    ).not.toBeNull();
  });

  it('shows the truncated success output when completed', () => {
    renderCard({ execState: 'completed', execResult: 'the answer is 42' });
    const el = document.querySelector('[data-hover-exec="completed"]')!;
    expect(el.textContent).toContain('the answer is 42');
  });

  it('shows the error message when this node errored', () => {
    renderCard({ execState: 'error', execError: 'kaboom' });
    const el = document.querySelector('[data-hover-exec="error"]')!;
    expect(el.textContent).toContain('kaboom');
  });

  it('shows the in/out field-count summary alongside other content', () => {
    renderCard({ description: 'x', inputCount: 3, outputCount: 2 });
    const io = document.querySelector('[data-hover-io]')!;
    expect(io).not.toBeNull();
    expect(io.textContent).toContain('3');
    expect(io.textContent).toContain('2');
  });

  it('counts alone do NOT open an otherwise-empty card', () => {
    renderCard({ inputCount: 3, outputCount: 2 }); // no desc/exec/warning
    expect(card()).toBeNull();
  });

  it('shows warnings (folded from the old __warnings__ tooltip)', () => {
    renderCard({ warnings: ['This loop node isn’t paired with its partner yet.'] });
    const el = document.querySelector('[data-hover-warnings]')!;
    expect(el.textContent).toContain('paired');
  });
});

describe('NodeHoverCard — no empty card', () => {
  afterEach(() => cleanup());

  it('renders NOTHING (no card) when there is no desc/result/warning', () => {
    renderCard({}); // forceOpen=true, but nothing to show
    expect(card()).toBeNull();
    // The trigger children still render — only the card is suppressed.
    expect(document.querySelector('[data-testid="node-body"]')).not.toBeNull();
  });

  it('hasNodeHoverContent is false for an empty node', () => {
    expect(
      hasNodeHoverContent({ description: '  ', execState: null, warnings: [] }),
    ).toBe(false);
  });

  it('hasNodeHoverContent is true with a description / exec / warning', () => {
    expect(
      hasNodeHoverContent({ description: 'x', execState: null, warnings: [] }),
    ).toBe(true);
    expect(
      hasNodeHoverContent({ execState: 'running', warnings: [] }),
    ).toBe(true);
    expect(
      hasNodeHoverContent({ execState: null, warnings: ['w'] }),
    ).toBe(true);
  });
});

describe('NodeHoverCard — conservative behaviors', () => {
  afterEach(() => cleanup());

  it('the card is pointer-transparent (does not capture the mouse)', () => {
    renderCard({ description: 'hi' });
    expect(card()!.className).toContain('pointer-events-none');
  });

  it('the card is bounded (max-w-[280px])', () => {
    renderCard({ description: 'hi' });
    expect(card()!.className).toContain('max-w-[280px]');
  });

  it('the card uses a side that does NOT cover the node (anchored right)', () => {
    renderCard({ description: 'hi' });
    // Radix reflects the resolved side onto the content element.
    expect(card()!.getAttribute('data-side')).toBe('right');
  });

  it('is forced SHUT while suppressed (canvas drag/connect OR inspector open)', () => {
    renderCard({ description: 'hi', suppressed: true });
    expect(card()).toBeNull();
  });
});
