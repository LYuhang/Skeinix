import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { UrlPreviewRenderer } from '../UrlPreviewRenderer';

describe('UrlPreviewRenderer', () => {
  it('opens an arbitrary HTTPS page in an isolated interactive frame', () => {
    render(
      <UrlPreviewRenderer
        url="https://example.com/docs"
        title="Reference"
        description="External documentation"
      />,
    );

    const frame = screen.getByTitle('Reference');
    expect(frame).toHaveAttribute('src', 'https://example.com/docs');
    expect(frame).toHaveAttribute('referrerpolicy', 'strict-origin-when-cross-origin');
    expect(frame.getAttribute('sandbox')).toContain('allow-scripts');
    expect(frame.getAttribute('sandbox')).toContain('allow-same-origin');
    expect(frame.getAttribute('sandbox')).toContain('allow-storage-access-by-user-activation');
    expect(screen.getByRole('link', { name: 'Open page in a new tab' })).toHaveAttribute(
      'href',
      'https://example.com/docs',
    );
  });

  it('navigates only after the user submits an HTTP(S) address', () => {
    render(<UrlPreviewRenderer url="https://example.com" title="Browser" />);

    const address = screen.getByRole('textbox', { name: 'Web address' });
    fireEvent.change(address, { target: { value: 'https://www.iana.org/domains/example' } });
    fireEvent.click(screen.getByRole('button', { name: 'Go' }));
    expect(screen.getByTitle('Browser')).toHaveAttribute(
      'src',
      'https://www.iana.org/domains/example',
    );

    fireEvent.change(address, { target: { value: 'javascript:alert(1)' } });
    expect(screen.getByRole('button', { name: 'Go' })).toBeDisabled();
  });

  it('clears the loading overlay when a sandboxed frame reports its native load event', async () => {
    render(<UrlPreviewRenderer url="https://example.com" title="Browser" />);

    expect(screen.getByText(/Loading/)).toBeInTheDocument();
    fireEvent.load(screen.getByTitle('Browser'));

    await waitFor(() => {
      expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
    });
  });

  it('does not leave an opaque loading overlay over cross-origin content', async () => {
    vi.useFakeTimers();
    try {
      render(<UrlPreviewRenderer url="https://example.com" title="Browser" />);
      expect(screen.getByText(/Loading/)).toBeInTheDocument();

      await act(async () => {
        vi.advanceTimersByTime(2_500);
      });

      expect(screen.queryByText(/Loading/)).not.toBeInTheDocument();
      expect(screen.getByTitle('Browser')).toHaveAttribute('src', 'https://example.com/');
    } finally {
      vi.useRealTimers();
    }
  });
});
