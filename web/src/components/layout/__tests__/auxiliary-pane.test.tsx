import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AuxiliaryPane } from '@/components/layout/auxiliary-pane';

describe('AuxiliaryPane', () => {
  it('is a nonmodal, resizable region and closes with Escape', () => {
    const onClose = vi.fn();
    render(
      <AuxiliaryPane
        open
        title="File preview"
        closeLabel="Close preview"
        resizeLabel="Resize preview"
        storageKey="test:auxiliary-pane"
        onClose={onClose}
      >
        <button type="button">Underlying-compatible content</button>
      </AuxiliaryPane>,
    );

    expect(screen.getByRole('region', { name: 'File preview' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('separator', { name: 'Resize preview' })).toBeInTheDocument();

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
