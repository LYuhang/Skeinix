import { useRef } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  IncrementalLogLoader,
  LogHistoryControls,
} from '@/components/logs/log-history-controls';

describe('LogHistoryControls', () => {
  it('exposes custom server-side time boundaries and sort changes', async () => {
    const onValueChange = vi.fn();
    const onOrderChange = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <LogHistoryControls
        value={{ range: 'all', from: '', to: '' }}
        order="desc"
        onValueChange={onValueChange}
        onOrderChange={onOrderChange}
      />,
    );

    await user.click(screen.getByRole('combobox', { name: 'Time range' }));
    await user.click(screen.getByRole('option', { name: 'custom' }));
    expect(onValueChange).toHaveBeenCalledWith({ range: 'custom', from: '', to: '' });

    rerender(
      <LogHistoryControls
        value={{ range: 'custom', from: '', to: '' }}
        order="desc"
        onValueChange={onValueChange}
        onOrderChange={onOrderChange}
      />,
    );
    fireEvent.change(screen.getByLabelText('From'), { target: { value: '2026-08-20T08:00' } });
    fireEvent.change(screen.getByLabelText('To'), { target: { value: '2026-08-22T18:00' } });
    expect(onValueChange).toHaveBeenCalledWith({
      range: 'custom',
      from: '2026-08-20T08:00',
      to: '',
    });
    expect(onValueChange).toHaveBeenCalledWith({
      range: 'custom',
      from: '',
      to: '2026-08-22T18:00',
    });

    await user.click(screen.getByRole('combobox', { name: 'Sort' }));
    await user.click(screen.getByRole('option', { name: 'Oldest first' }));
    expect(onOrderChange).toHaveBeenCalledWith('asc');
  });
});

describe('IncrementalLogLoader', () => {
  const originalObserver = globalThis.IntersectionObserver;

  afterEach(() => {
    if (originalObserver) globalThis.IntersectionObserver = originalObserver;
    else Reflect.deleteProperty(globalThis, 'IntersectionObserver');
  });

  it('keeps an accessible manual fallback and describes the active direction', async () => {
    const onLoadMore = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(
      <IncrementalLogLoader hasMore loading={false} onLoadMore={onLoadMore} order="desc" />,
    );

    await user.click(screen.getByRole('button', { name: 'Load older records' }));
    expect(onLoadMore).toHaveBeenCalledTimes(1);

    rerender(
      <IncrementalLogLoader hasMore loading={false} onLoadMore={onLoadMore} order="asc" />,
    );
    expect(screen.getByRole('button', { name: 'Load newer records' })).toBeInTheDocument();
  });

  it('observes the boundary against the contained log viewport and auto-loads once visible', () => {
    let callback: IntersectionObserverCallback | undefined;
    let options: IntersectionObserverInit | undefined;
    const observe = vi.fn();
    const disconnect = vi.fn();
    class MockIntersectionObserver implements IntersectionObserver {
      readonly root = null;
      readonly rootMargin = '';
      readonly thresholds: readonly number[] = [];

      constructor(nextCallback: IntersectionObserverCallback, nextOptions?: IntersectionObserverInit) {
        callback = nextCallback;
        options = nextOptions;
      }

      observe = observe;
      disconnect = disconnect;
      unobserve = vi.fn();
      takeRecords = vi.fn(() => []);
    }
    globalThis.IntersectionObserver = MockIntersectionObserver;
    const onLoadMore = vi.fn();

    function Harness() {
      const rootRef = useRef<HTMLDivElement>(null);
      return (
        <div ref={rootRef} data-testid="log-root">
          <IncrementalLogLoader
            hasMore
            loading={false}
            onLoadMore={onLoadMore}
            order="desc"
            rootRef={rootRef}
          />
        </div>
      );
    }

    const { unmount } = render(<Harness />);
    expect(options?.root).toBe(screen.getByTestId('log-root'));
    expect(options?.rootMargin).toBe('160px');
    expect(observe).toHaveBeenCalledTimes(1);

    callback?.([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
    expect(onLoadMore).toHaveBeenCalledTimes(1);
    unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
  });

  it('does not render or observe when the cursor has no next page', () => {
    const observer = vi.fn();
    globalThis.IntersectionObserver = observer as unknown as typeof IntersectionObserver;
    render(
      <IncrementalLogLoader hasMore={false} loading={false} onLoadMore={vi.fn()} order="desc" />,
    );
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(observer).not.toHaveBeenCalled();
  });
});
