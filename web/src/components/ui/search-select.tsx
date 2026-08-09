import { useDeferredValue, useEffect, useId, useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, ReactNode } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Check, ChevronDown, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import { cn } from '@/lib/utils';

export interface SearchSelectOption {
  value: string;
  label: string;
  description?: string | null;
  meta?: string | null;
  keywords?: string[];
  disabled?: boolean;
}

function highlightText(text: string, query: string) {
  const q = query.trim();
  if (!q) return text;
  const lower = text.toLocaleLowerCase();
  const needle = q.toLocaleLowerCase();
  const parts: ReactNode[] = [];
  let cursor = 0;
  let index = lower.indexOf(needle);
  while (index >= 0) {
    if (index > cursor) parts.push(text.slice(cursor, index));
    parts.push(
      <mark key={`${index}-${needle}`} className="bg-transparent font-semibold text-focus">
        {text.slice(index, index + needle.length)}
      </mark>,
    );
    cursor = index + needle.length;
    index = lower.indexOf(needle, cursor);
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts;
}

function optionSearchText(option: SearchSelectOption): string {
  return [
    option.label,
    option.description ?? '',
    option.meta ?? '',
    ...(option.keywords ?? []),
  ].join(' ').toLocaleLowerCase();
}

export interface SearchSelectProps {
  id?: string;
  value: string;
  options: SearchSelectOption[];
  onValueChange: (value: string) => void;
  placeholder: string;
  searchPlaceholder?: string;
  emptyText?: string;
  disabled?: boolean;
  className?: string;
  triggerClassName?: string;
  triggerTestId?: string;
  /**
   * Optional host for the floating list. Pass the containing Dialog element so
   * Radix treats the list as part of that modal's interaction boundary.
   */
  portalContainer?: HTMLElement | null;
  renderOption?: (option: SearchSelectOption, query: string) => ReactNode;
}

export function SearchSelect({
  id,
  value,
  options,
  onValueChange,
  placeholder,
  searchPlaceholder,
  emptyText,
  disabled = false,
  className,
  triggerClassName,
  triggerTestId,
  portalContainer,
  renderOption,
}: SearchSelectProps) {
  const { t } = useTranslation();
  const listboxId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const [activeIndex, setActiveIndex] = useState(0);
  const selected = useMemo(
    () => options.find((option) => option.value === value) ?? null,
    [options, value],
  );
  const filtered = useMemo(() => {
    const q = deferredQuery.trim().toLocaleLowerCase();
    if (!q) return options;
    return options.filter((option) => optionSearchText(option).includes(q));
  }, [deferredQuery, options]);

  useEffect(() => {
    if (!open) return;
    const firstEnabled = filtered.findIndex((option) => !option.disabled);
    queueMicrotask(() => setActiveIndex(firstEnabled >= 0 ? firstEnabled : 0));
  }, [filtered, open]);

  useEffect(() => {
    if (!open) return;
    optionRefs.current[activeIndex]?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, open]);

  const close = () => {
    setOpen(false);
    setQuery('');
  };

  const selectOption = (option: SearchSelectOption | undefined) => {
    if (!option || option.disabled) return;
    onValueChange(option.value);
    close();
  };

  const moveActive = (direction: 1 | -1) => {
    if (filtered.length === 0) return;
    let next = activeIndex;
    for (let count = 0; count < filtered.length; count += 1) {
      next = (next + direction + filtered.length) % filtered.length;
      if (!filtered[next]?.disabled) {
        setActiveIndex(next);
        return;
      }
    }
  };

  const onInputKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      moveActive(1);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      moveActive(-1);
    } else if (event.key === 'Home') {
      event.preventDefault();
      const index = filtered.findIndex((option) => !option.disabled);
      if (index >= 0) setActiveIndex(index);
    } else if (event.key === 'End') {
      event.preventDefault();
      const reversedIndex = [...filtered].reverse().findIndex((option) => !option.disabled);
      if (reversedIndex >= 0) setActiveIndex(filtered.length - 1 - reversedIndex);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      selectOption(filtered[activeIndex]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  };

  const activeOption = filtered[activeIndex];

  return (
    <Popover.Root
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (!nextOpen) setQuery('');
      }}
    >
      <div className={cn('min-w-0', className)}>
        <Popover.Trigger asChild>
          <button
            id={id}
            type="button"
            data-testid={triggerTestId}
            disabled={disabled}
            aria-haspopup="listbox"
            aria-expanded={open}
            aria-controls={open ? listboxId : undefined}
            className={cn(
              'flex h-9 w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 py-2 text-left text-sm transition-colors duration-feedback hover:bg-muted/30 focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-50',
              triggerClassName,
            )}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                setOpen(true);
              }
            }}
          >
            <span className={cn('min-w-0 flex-1 truncate', !selected && 'text-muted-foreground')}>
              {selected ? selected.label : placeholder}
            </span>
            <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          </button>
        </Popover.Trigger>
      </div>

      <Popover.Portal container={portalContainer ?? undefined}>
        <Popover.Content
          align="start"
          sideOffset={6}
          collisionPadding={12}
          className="pointer-events-auto z-modal-popover w-[var(--radix-popover-trigger-width)] max-w-[calc(100vw-1.5rem)] overflow-hidden rounded-md border border-edge-structural bg-popover text-popover-foreground shadow-popover duration-popover data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 motion-reduce:animate-none"
        >
          <div className="sticky top-0 z-[1] flex items-center border-b border-edge-subtle bg-popover px-3">
            <Search className="mr-2 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
            <input
              ref={inputRef}
              role="combobox"
              aria-autocomplete="list"
              aria-expanded={open}
              aria-controls={listboxId}
              aria-activedescendant={activeOption ? `${listboxId}-${activeIndex}` : undefined}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={onInputKeyDown}
              placeholder={searchPlaceholder ?? `${t('common_search')}…`}
              className="h-10 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <div
            id={listboxId}
            data-role="search-select-options"
            className="max-h-[min(var(--radix-popover-content-available-height),18rem)] overflow-y-auto overscroll-contain p-1"
            role="listbox"
            onWheel={(event) => {
              // Do not hand an in-progress list scroll to an overflowed parent
              // Dialog. At either boundary normal scroll chaining is preserved.
              const list = event.currentTarget;
              const canScrollUp = list.scrollTop > 0;
              const canScrollDown = list.scrollTop + list.clientHeight < list.scrollHeight;
              if ((event.deltaY < 0 && canScrollUp) || (event.deltaY > 0 && canScrollDown)) {
                event.stopPropagation();
              }
            }}
          >
            {filtered.length === 0 ? (
              <div className="px-3 py-6 text-center text-sm text-muted-foreground">
                {emptyText ?? t('no_match')}
              </div>
            ) : (
              filtered.map((option, index) => {
                const selectedOption = option.value === value;
                const active = index === activeIndex;
                return (
                  <button
                    key={option.value}
                    id={`${listboxId}-${index}`}
                    ref={(node) => { optionRefs.current[index] = node; }}
                    type="button"
                    role="option"
                    aria-selected={selectedOption}
                    aria-disabled={option.disabled || undefined}
                    disabled={option.disabled}
                    tabIndex={-1}
                    className={cn(
                      'flex min-h-9 w-full items-start gap-2 rounded-sm px-2 py-2 text-left text-sm outline-none transition-colors duration-feedback disabled:opacity-50',
                      active && 'bg-accent text-accent-foreground',
                      selectedOption && 'bg-accent/70 font-medium',
                    )}
                    onPointerMove={() => setActiveIndex(index)}
                    onClick={() => selectOption(option)}
                  >
                    <Check
                      className={cn('mt-0.5 h-4 w-4 shrink-0', selectedOption ? 'opacity-100' : 'opacity-0')}
                      aria-hidden="true"
                    />
                    <div className="min-w-0 flex-1">
                      {renderOption ? (
                        renderOption(option, query)
                      ) : (
                        <>
                          <div className="truncate font-medium">
                            {highlightText(option.label, query)}
                          </div>
                          {(option.meta || option.description) ? (
                            <div className="mt-0.5 truncate text-xs text-muted-foreground">
                              {option.meta ? highlightText(option.meta, query) : null}
                              {option.meta && option.description ? ' · ' : null}
                              {option.description ? highlightText(option.description, query) : null}
                            </div>
                          ) : null}
                        </>
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
