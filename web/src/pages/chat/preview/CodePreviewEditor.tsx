import { useCallback, useMemo, type KeyboardEvent } from 'react';
import CodeMirror from '@uiw/react-codemirror';
import { indentWithTab } from '@codemirror/commands';
import { python } from '@codemirror/lang-python';
import { indentUnit } from '@codemirror/language';
import { EditorState, type Extension } from '@codemirror/state';
import { EditorView, keymap } from '@codemirror/view';
import { useTheme } from 'next-themes';

import type { CodePreviewLanguage } from './code-preview-language';

const BASE_EXTENSIONS: Extension[] = [
  keymap.of([indentWithTab]),
  EditorState.tabSize.of(4),
  EditorView.lineWrapping,
];

const PYTHON_EXTENSIONS: Extension[] = [
  ...BASE_EXTENSIONS,
  indentUnit.of('    '),
  python(),
];

export interface CodePreviewEditorProps {
  value: string;
  language: CodePreviewLanguage;
  readOnly: boolean;
  ariaLabel: string;
  onChange: (next: string) => void;
}

/** Professional code surface shared by every file opened through Preview. */
export function CodePreviewEditor({
  value,
  language,
  readOnly,
  ariaLabel,
  onChange,
}: CodePreviewEditorProps) {
  const { resolvedTheme } = useTheme();
  const extensions = language === 'python' ? PYTHON_EXTENSIONS : BASE_EXTENSIONS;
  const basicSetup = useMemo(
    () => ({
      lineNumbers: true,
      foldGutter: true,
      highlightActiveLine: !readOnly,
      highlightActiveLineGutter: !readOnly,
    }),
    [readOnly],
  );
  const handleChange = useCallback((next: string) => onChange(next), [onChange]);
  const stopKeyPropagation = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    event.stopPropagation();
  }, []);

  return (
    <div
      className="h-full min-h-0 overflow-hidden bg-background text-xs [&_.cm-content]:min-h-full [&_.cm-editor]:h-full [&_.cm-focused]:outline-none [&_.cm-gutters]:border-r [&_.cm-gutters]:border-edge-subtle [&_.cm-gutters]:bg-muted/30 [&_.cm-line]:font-mono [&_.cm-line]:leading-5 [&_.cm-scroller]:font-mono"
      onKeyDown={stopKeyPropagation}
      data-role="code-preview-editor"
    >
      <CodeMirror
        value={value}
        editable={!readOnly}
        readOnly={readOnly}
        aria-label={ariaLabel}
        onChange={handleChange}
        extensions={extensions}
        basicSetup={basicSetup}
        height="100%"
        theme={resolvedTheme === 'dark' ? 'dark' : 'light'}
      />
    </div>
  );
}
