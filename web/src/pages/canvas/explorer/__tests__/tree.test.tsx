import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { CollapsibleFolder } from '@/pages/canvas/explorer/CollapsibleFolder';

// NOTE: the VfsFilesSection / WorkflowVersionsSection component tests live in
// sections.test.tsx (a SINGLE file), NOT here — two sibling files mocking the
// same modules (react-router / queries.workflow / api.vfs) collide under
// vitest isolate=false ([[feedback_vitest_isolate_false]]). This file mocks
// NOTHING, so it can never collide.

describe('CollapsibleFolder', () => {
  it('hides children when closed, shows on toggle', () => {
    const onToggle = vi.fn();
    const { rerender } = render(
      <CollapsibleFolder label="data" depth={0} open={false} onToggle={onToggle}>
        <div>child</div>
      </CollapsibleFolder>,
    );
    expect(screen.queryByText('child')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /data/ }));
    expect(onToggle).toHaveBeenCalled();
    rerender(
      <CollapsibleFolder label="data" depth={0} open onToggle={onToggle}>
        <div>child</div>
      </CollapsibleFolder>,
    );
    expect(screen.getByText('child')).toBeInTheDocument();
  });

  it('selects and toggles a tree folder from the entire row with one click', () => {
    const onSelect = vi.fn();
    const onToggle = vi.fn();
    render(
      <CollapsibleFolder
        label="images"
        depth={1}
        open={false}
        onToggle={onToggle}
        onSelect={onSelect}
        treeItem
      />,
    );

    fireEvent.click(screen.getByRole('treeitem', { name: /images/ }));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
