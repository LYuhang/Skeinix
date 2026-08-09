/**
 * Conservative node hover card.
 *
 * Two-tier affordance: hover = a transient read-only peek; click = open the
 * Inspector (full, editable). This component is JUST the hover tier. It folds
 * the two Radix tooltips that used to live in `CustomNode` (the `__warnings__`
 * tooltip + the exec-error tooltip) into ONE bounded card, plus surfaces the
 * node description and a truncated exec result.
 *
 * The load-bearing CONSERVATIVE constraints (the user explicitly wants
 * restraint:
 *   - **Open delay ~500ms** (`openDelay`) so an incidental mouse-over never
 *     flickers a card; hides immediately on mouse-leave (`closeDelay=0`).
 *   - **Bounded** `max-w-[280px]`; every text region truncates with `…` — the
 *     full content lives in the Inspector (click), not a scrollable block.
 *   - **Pointer-transparent** (`pointer-events-none` on the content) so the
 *     card never captures the mouse or blocks a click on the node underneath.
 *   - **Never covers the hovered node**: `side="right"` + a sideOffset keeps
 *     the breathing ring visible; Radix collision-avoidance flips the card to
 *     stay in the viewport (`collisionPadding`) without us covering the anchor.
 *   - **Subtle fade**, no scale/slide.
 *   - **No empty card**: if there is no description, no exec result, and no
 *     warning, `hasContent` is false and we render NOTHING (the trigger still
 *     renders its children, but no card opens).
 *   - **Suppressed** while `suppressed` is true (the caller passes
 *     `canvasInteracting || nodeOpenInInspector`) — `open={false}` forces the
 *     card shut regardless of hover.
 *
 * The trigger wraps the node body; the always-visible ⚠ badge + exec
 * indicators stay in `CustomNode` so problems show WITHOUT hovering — only the
 * DETAIL moved here.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import * as HoverCardPrimitive from '@radix-ui/react-hover-card';
import { cn } from '@/lib/utils';
import type { NodeExecState } from '@/pages/canvas/nodes/CustomNode';
import { hasNodeHoverContent } from './node-hover-utils';

export interface NodeHoverCardProps {
  /** User-facing node title (node_name, else node_id). */
  title: string;
  /** Human label for the node type (already localized upstream). */
  typeLabel: string;
  /** Optional free-text node description (truncated to a few lines). */
  description?: string;
  /** This node's narrowed exec state, or null if it didn't run this pass. */
  execState: NodeExecState;
  /** Truncated success-output preview (only meaningful when completed). */
  execResult?: string;
  /** Error message (only meaningful when execState === 'error'). */
  execError?: string;
  /** Resolved (already `t()`'d) warning lines; empty ⟺ no warnings. */
  warnings: string[];
  /**
   * Compact field summary (counts only). The canvas card no longer lists
   * fields inline, so the hover card surfaces an `Inputs N · Outputs M` line as
   * a peek of the node's shape. Auxiliary only — it does NOT make the card open
   * on its own (see `hasNodeHoverContent`); it rides alongside a description /
   * exec / warning.
   */
  inputCount?: number;
  outputCount?: number;
  /**
   * When true the card is forced shut while canvas drag/connect is in progress,
   * OR this node is already open in the Inspector → the card is redundant).
   */
  suppressed: boolean;
  /** The node body — becomes the hover trigger. */
  children: ReactNode;
}

/** Open delay so incidental mouse-overs do not flicker a card. */
const OPEN_DELAY_MS = 500;

/**
 * Avoid empty cards: the card needs content beyond the
 * name/type header: a description, an exec state, or a warning. Exported so the
 * render decision is unit-testable without standing up Radix hover timing.
 */
export function NodeHoverCard({
  title,
  typeLabel,
  description,
  execState,
  execResult,
  execError,
  warnings,
  inputCount,
  outputCount,
  suppressed,
  children,
  /**
   * Test-only forced open state. Production leaves this `undefined` so Radix
   * drives open from hover + the 500ms delay; the suppress/no-empty rules below
   * still force it SHUT regardless. Tests pass `forceOpen` to inspect the
   * rendered content (jsdom can't reproduce the hover-delay timing).
   */
  forceOpen,
}: NodeHoverCardProps & { forceOpen?: boolean }) {
  const { t } = useTranslation();

  const desc = description?.trim() || '';
  const hasWarnings = warnings.length > 0;
  const hasContent = hasNodeHoverContent({ description, execState, warnings });

  // Keep Radix controlled for the component's entire lifetime. Switching its
  // `open` prop between `undefined` and `false` when suppression changes makes
  // the primitive alternate between uncontrolled and controlled modes.
  const [hoverOpen, setHoverOpen] = useState(Boolean(forceOpen));
  const open = !suppressed && hasContent && (forceOpen ?? hoverOpen);

  useEffect(() => {
    if (suppressed || !hasContent) queueMicrotask(() => setHoverOpen(false));
  }, [hasContent, suppressed]);

  return (
    <HoverCardPrimitive.Root
      openDelay={OPEN_DELAY_MS}
      closeDelay={0}
      open={open}
      onOpenChange={(nextOpen) => {
        if (forceOpen === undefined) {
          setHoverOpen(!suppressed && hasContent && nextOpen);
        }
      }}
    >
      <HoverCardPrimitive.Trigger asChild>{children}</HoverCardPrimitive.Trigger>
      <HoverCardPrimitive.Portal>
        <HoverCardPrimitive.Content
          data-node-hover-card
          side="right"
          align="start"
          sideOffset={12}
          collisionPadding={12}
          avoidCollisions
          className={cn(
            // Bounded width; pointer-transparent so it never blocks clicks /
            // captures the mouse; subtle fade only (no scale/slide). When a
            // rendered template preview is shown the card widens to fit the
            // bounded iframe (the iframe container is pointer-events-auto so it
            // stays scrollable even though the card body is pointer-none).
            'pointer-events-none z-50 rounded-md border bg-popover',
            'max-w-[280px]',
            'px-3 py-2 text-popover-foreground shadow-md',
            'animate-in fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0',
          )}
        >
          {/* Header — name + type (always present). */}
          <div className="flex flex-col gap-0.5">
            <span className="truncate text-sm font-medium" title={title}>
              {title}
            </span>
            <span className="truncate text-xs text-muted-foreground">
              {typeLabel}
            </span>
          </div>

          {/* Compact field summary — the peek of node shape the card lost when
              the canvas card dropped its inline field list. */}
          {(inputCount !== undefined || outputCount !== undefined) && (
            <p
              data-hover-io
              className="mt-1.5 text-xs tabular-nums text-muted-foreground"
            >
              {t('node_inputs', 'Inputs')} {inputCount ?? 0}
              <span className="mx-1 text-muted-foreground/40">·</span>
              {t('node_outputs', 'Outputs')} {outputCount ?? 0}
            </p>
          )}

          {/* Description — truncated to ~2-3 lines with an ellipsis. */}
          {desc && (
            <p
              data-hover-description
              className="mt-1.5 line-clamp-3 text-xs text-muted-foreground"
            >
              {desc}
            </p>
          )}

          {/* Exec result — only when this node ran this pass. */}
          {execState === 'running' && (
            <p
              data-hover-exec="running"
              className="mt-1.5 truncate text-xs text-state-running"
            >
              ◍ {t('canvas.exec.running', 'Running…')}
            </p>
          )}
          {execState === 'completed' && (
            <p
              data-hover-exec="completed"
              className="mt-1.5 line-clamp-2 break-all text-xs text-state-success"
            >
              ✓ {execResult?.trim() || t('canvas.exec.completed', 'Completed')}
            </p>
          )}
          {execState === 'error' && (
            <p
              data-hover-exec="error"
              className="mt-1.5 line-clamp-2 break-all text-xs text-state-danger"
            >
              ✗ {execError?.trim() || t('canvas.exec.error', 'Failed')}
            </p>
          )}

          {/* Warnings — folded from the old __warnings__ tooltip. */}
          {hasWarnings && (
            <ul
              data-hover-warnings
              className="mt-1.5 flex list-none flex-col gap-0.5 text-xs text-state-warning"
            >
              {warnings.map((w) => (
                <li key={w} className="line-clamp-2">
                  ⚠ {w}
                </li>
              ))}
            </ul>
          )}
        </HoverCardPrimitive.Content>
      </HoverCardPrimitive.Portal>
    </HoverCardPrimitive.Root>
  );
}
