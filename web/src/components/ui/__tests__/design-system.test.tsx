import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { SearchSelect } from '@/components/ui/search-select';
import { ProgressState, StatusBadge } from '@/components/ui/status';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  PaneResizeHandle,
} from '@/components/ui/pane-resize-handle';
import { usePersistedPaneWidth } from '@/components/ui/use-persisted-pane-width';
import { setLocale } from '@/lib/i18n';
import { AsyncState } from '@/components/ui/async-state';

beforeEach(() => {
  window.localStorage.clear();
  Element.prototype.scrollIntoView = vi.fn();
  setLocale('en');
});

describe('semantic design primitives', () => {
  it.each([
    ['loading', 'status'],
    ['empty', 'status'],
    ['error', 'alert'],
    ['partial', 'status'],
    ['permission', 'alert'],
    ['disabled', 'status'],
    ['success', 'status'],
  ] as const)('renders the %s page-state contract', (kind, role) => {
    render(<AsyncState kind={kind} title={`${kind} state`} />);
    expect(screen.getByRole(role)).toHaveTextContent(`${kind} state`);
  });

  it('renders semantic status with text plus a non-color status marker', () => {
    render(<StatusBadge status="warning">Awaiting approval</StatusBadge>);
    expect(screen.getByText('Awaiting approval')).toBeInTheDocument();
    expect(screen.getByText('Awaiting approval')).toHaveClass('text-state-warning');
  });

  it('uses the rendered ReactNode label as the progressbar accessible name', () => {
    render(
      <ProgressState
        label={<span>任务进度</span>}
        value={50}
      />,
    );

    expect(screen.getByRole('progressbar', { name: '任务进度' }))
      .toHaveAttribute('aria-labelledby');
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('aria-label', 'Progress');
  });

  it.each([
    ['en', 'Technical details'],
    ['zh', '技术详情'],
  ] as const)('localizes the AsyncState technical-details fallback in %s', (locale, label) => {
    setLocale(locale);
    render(
      <AsyncState
        kind="error"
        title="Failure"
        technicalDetails="Internal diagnostics"
      />,
    );

    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it('supports explicit underline and vertical tab grammars', () => {
    const { rerender } = render(
      <Tabs defaultValue="one">
        <TabsList variant="underline">
          <TabsTrigger value="one">One</TabsTrigger>
        </TabsList>
      </Tabs>,
    );
    expect(screen.getByRole('tablist')).toHaveClass('border-b');
    expect(screen.getByRole('tab')).toHaveClass('border-b-2');

    rerender(
      <Tabs defaultValue="one" orientation="vertical">
        <TabsList variant="vertical">
          <TabsTrigger value="one">One</TabsTrigger>
        </TabsList>
      </Tabs>,
    );
    expect(screen.getByRole('tablist')).toHaveClass('flex-col');
    expect(screen.getByRole('tab')).toHaveClass('justify-start');
  });

  it('portals searchable options and supports arrow-key selection', async () => {
    const user = userEvent.setup();
    const onValueChange = vi.fn();
    const { container } = render(
      <div className="overflow-hidden">
        <SearchSelect
          value=""
          placeholder="Choose model"
          options={[
            { value: 'a', label: 'Alpha' },
            { value: 'b', label: 'Beta' },
          ]}
          onValueChange={onValueChange}
        />
      </div>,
    );

    await user.click(screen.getByRole('button', { name: 'Choose model' }));
    const combobox = screen.getByRole('combobox');
    expect(screen.getByRole('listbox')).toBeInTheDocument();
    expect(container.querySelector('[role="listbox"]')).toBeNull();
    await user.keyboard('{ArrowDown}{Enter}');
    expect(combobox).not.toBeInTheDocument();
    expect(onValueChange).toHaveBeenCalledWith('b');
  });

  it('gives the SearchSelect input a localized accessible name', async () => {
    setLocale('zh');
    const user = userEvent.setup();
    render(
      <SearchSelect
        value=""
        placeholder="选择模型"
        options={[]}
        onValueChange={vi.fn()}
      />,
    );

    await user.click(screen.getByRole('button', { name: '选择模型' }));
    expect(screen.getByRole('combobox', { name: '搜索' })).toBeInTheDocument();
  });

  it('keeps document language synchronized with the selected locale', () => {
    setLocale('en');
    expect(document.documentElement.lang).toBe('en');
    setLocale('zh');
    expect(document.documentElement.lang).toBe('zh-CN');
  });
});

function PaneHarness() {
  const pane = usePersistedPaneWidth({
    storageKey: 'test:pane-width',
    defaultWidth: 300,
    minWidth: 240,
    maxWidth: 420,
  });
  return (
    <div data-testid="width" data-width={pane.width}>
      <PaneResizeHandle
        side="right"
        width={pane.width}
        minWidth={240}
        maxWidth={420}
        onWidthChange={pane.setWidth}
        onReset={pane.resetWidth}
        label="Resize Explorer"
      />
    </div>
  );
}

describe('persisted pane resizing', () => {
  it('supports keyboard adjustment, clamping, persistence, and reset', () => {
    vi.useFakeTimers();
    try {
      render(<PaneHarness />);
      const handle = screen.getByRole('separator', { name: 'Resize Explorer' });
      fireEvent.keyDown(handle, { key: 'ArrowRight' });
      expect(screen.getByTestId('width')).toHaveAttribute('data-width', '316');
      expect(window.localStorage.getItem('test:pane-width')).toBeNull();
      act(() => vi.advanceTimersByTime(160));
      expect(window.localStorage.getItem('test:pane-width')).toBe('316');

      fireEvent.keyDown(handle, { key: 'End' });
      expect(screen.getByTestId('width')).toHaveAttribute('data-width', '420');
      act(() => vi.advanceTimersByTime(160));
      expect(window.localStorage.getItem('test:pane-width')).toBe('420');

      fireEvent.doubleClick(handle);
      expect(screen.getByTestId('width')).toHaveAttribute('data-width', '300');
      act(() => vi.advanceTimersByTime(160));
      expect(window.localStorage.getItem('test:pane-width')).toBe('300');
    } finally {
      vi.useRealTimers();
    }
  });
});
