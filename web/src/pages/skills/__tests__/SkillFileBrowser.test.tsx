import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { SkillFileBrowser } from '@/pages/skills/SkillFileBrowser';

describe('<SkillFileBrowser>', () => {
  it('renders nested files as an explorer and loads file content in the detail pane', async () => {
    const user = userEvent.setup();
    const loadFile = vi.fn(async (path: string) => new Blob(
      [path === 'scripts/helpers/format.py' ? 'def format_value(value):\n    return str(value)' : ''],
      { type: 'text/x-python' },
    ));

    render(
      <SkillFileBrowser
        files={['scripts/helpers/format.py', 'references/guide.md']}
        skillMd={'# Example Skill\n\nInstructions.'}
        loadFile={loadFile}
        labels={{ files: 'Package Files', loading: 'Loading File…', failed: 'Could Not Load File', binary: 'No Preview' }}
      />,
    );

    expect(screen.getByRole('tree', { name: 'Package Files' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'scripts' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'helpers' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'format.py' }));

    expect(loadFile).toHaveBeenCalledWith('scripts/helpers/format.py');
    expect(await screen.findByText(/def format_value\(value\):/)).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('supports roving tree keyboard navigation and reports the selected path', async () => {
    const user = userEvent.setup();
    const onSelectedPathChange = vi.fn();
    const loadFile = vi.fn(async () => new Blob(['print("ok")'], { type: 'text/x-python' }));

    render(
      <SkillFileBrowser
        files={['scripts/helpers/format.py']}
        skillMd="# Skill"
        loadFile={loadFile}
        onSelectedPathChange={onSelectedPathChange}
        labels={{ files: 'Package Files', loading: 'Loading File…', failed: 'Could Not Load File', binary: 'No Preview' }}
      />,
    );

    const scripts = screen.getByRole('button', { name: 'scripts' });
    scripts.focus();
    await user.keyboard('{ArrowRight}{ArrowRight}{ArrowRight}{Enter}');

    expect(loadFile).toHaveBeenCalledWith('scripts/helpers/format.py');
    expect(onSelectedPathChange).toHaveBeenCalledWith('scripts/helpers/format.py');
    expect(screen.getByRole('treeitem', { selected: true })).toHaveTextContent('format.py');
  });
});
