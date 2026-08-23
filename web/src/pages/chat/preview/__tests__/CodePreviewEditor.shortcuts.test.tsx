import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CodePreviewEditor } from '../CodePreviewEditor';

describe('CodePreviewEditor desktop editing contract', () => {
  it('advertises and retains standard clipboard/history shortcuts', () => {
    render(
      <CodePreviewEditor
        value={'print("hello")\n'}
        language={{ id: 'Python', description: null }}
        readOnly={false}
        ariaLabel="example.py source"
        onChange={() => undefined}
      />,
    );

    const editor = screen.getByRole('textbox', { name: 'example.py source' });
    const surface = editor.closest('[data-role="code-preview-editor"]');
    expect(surface).toHaveAttribute(
      'aria-keyshortcuts',
      'Control+C Meta+C Control+V Meta+V Control+Z Meta+Z Control+Y Meta+Shift+Z',
    );
    expect(editor).toHaveAttribute('contenteditable', 'true');
  });
});
