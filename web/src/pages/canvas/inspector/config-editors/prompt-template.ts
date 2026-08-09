/**
 * Pure helpers for the PromptNode `prompt_template` editor.
 *
 *   - `placeholderHighlight` — a CodeMirror ViewPlugin that paints a light
 *     green background over every `{{xxx}}` interpolation slot (live).
 *   - `missingOutputFields` — given a template and the node's OUTPUT field
 *     names, returns the names that are NOT referenced as a quoted string
 *     (`"name"` or `'name'`) anywhere in the template. The PromptNode is
 *     expected to emit a JSON object whose keys are the output fields, so a
 *     bare un-quoted mention does NOT count.
 */
import { RangeSetBuilder } from '@codemirror/state';
import {
  Decoration,
  type DecorationSet,
  EditorView,
  ViewPlugin,
  type ViewUpdate,
} from '@codemirror/view';

const PLACEHOLDER_RE = /\{\{[^}]*\}\}/g;

const placeholderMark = Decoration.mark({ class: 'cm-placeholder-hl' });

function buildPlaceholderDecorations(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  for (const { from, to } of view.visibleRanges) {
    const text = view.state.doc.sliceString(from, to);
    PLACEHOLDER_RE.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = PLACEHOLDER_RE.exec(text)) !== null) {
      const start = from + m.index;
      const end = start + m[0].length;
      builder.add(start, end, placeholderMark);
      // Guard against a zero-length match (can't happen with this regex, but
      // keeps the loop safe).
      if (m.index === PLACEHOLDER_RE.lastIndex) PLACEHOLDER_RE.lastIndex++;
    }
  }
  return builder.finish();
}

/** ViewPlugin that highlights `{{...}}` placeholders, updating live. */
export const placeholderHighlight = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = buildPlaceholderDecorations(view);
    }
    update(u: ViewUpdate) {
      if (u.docChanged || u.viewportChanged) {
        this.decorations = buildPlaceholderDecorations(u.view);
      }
    }
  },
  { decorations: (v) => v.decorations },
);

/** Theme extension defining the placeholder highlight class (self-contained). */
export const placeholderTheme = EditorView.theme({
  '.cm-placeholder-hl': {
    backgroundColor: 'rgba(34, 197, 94, 0.18)', // light green, theme-neutral
    borderRadius: '2px',
  },
});

/** Escape a string for safe insertion into a RegExp. */
function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Output field names NOT referenced in the template as a quoted string.
 *
 * A field `name` counts as present iff the template contains `"name"` OR
 * `'name'`. A bare, un-quoted mention does NOT count. Empty field names are
 * ignored. Returns the missing names in their original order.
 */
export function missingOutputFields(
  template: string,
  outputNames: readonly string[],
): string[] {
  const missing: string[] = [];
  for (const name of outputNames) {
    if (!name) continue;
    const esc = escapeRegExp(name);
    const re = new RegExp(`["']${esc}["']`);
    if (!re.test(template)) missing.push(name);
  }
  return missing;
}
