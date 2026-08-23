import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import i18n from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { IncrementalLogLoader } from '@/components/logs/log-history-controls';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

describe('<IncrementalLogLoader>', () => {
  let observerCallback: IntersectionObserverCallback;
  const observe = vi.fn();
  const disconnect = vi.fn();
  const root = document.createElement('div');

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal('IntersectionObserver', class implements IntersectionObserver {
      readonly root: Element | Document | null;
      readonly rootMargin: string;
      readonly thresholds = [0];
      observe = observe;
      disconnect = disconnect;
      unobserve = vi.fn();
      takeRecords = vi.fn(() => []);

      constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
        observerCallback = callback;
        expect(options).toMatchObject({ root, rootMargin: '160px' });
        this.root = options?.root ?? null;
        this.rootMargin = options?.rootMargin ?? '0px';
      }
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('loads automatically at the contained-scroll boundary and disconnects cleanly', async () => {
    const onLoadMore = vi.fn();
    const { unmount } = render(
      <I18nextProvider i18n={testI18n}>
        <IncrementalLogLoader
          hasMore
          loading={false}
          onLoadMore={onLoadMore}
          order="desc"
          rootRef={{ current: root }}
        />
      </I18nextProvider>,
    );

    expect(observe).toHaveBeenCalledOnce();
    observerCallback([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
    await waitFor(() => expect(onLoadMore).toHaveBeenCalledOnce());

    unmount();
    expect(disconnect).toHaveBeenCalledOnce();
  });

  it('keeps an accessible manual fallback whose direction follows the sort order', async () => {
    const user = userEvent.setup();
    const onLoadMore = vi.fn();
    render(
      <I18nextProvider i18n={testI18n}>
        <IncrementalLogLoader
          hasMore
          loading={false}
          onLoadMore={onLoadMore}
          order="asc"
          rootRef={{ current: root }}
        />
      </I18nextProvider>,
    );

    await user.click(screen.getByRole('button', { name: 'Load newer records' }));
    expect(onLoadMore).toHaveBeenCalledOnce();
  });
});
