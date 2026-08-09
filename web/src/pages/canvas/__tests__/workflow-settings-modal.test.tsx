import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';

import { WorkflowSettingsModal } from '@/pages/canvas/WorkflowSettingsModal';
import { useWorkflowEditStore } from '@/stores/workflow-edit';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderModal(onOpenChange: (open: boolean) => void = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={testI18n}>
        <WorkflowSettingsModal open={true} onOpenChange={onOpenChange} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const settingsOf = () => {
  const meta = useWorkflowEditStore.getState().draft!.__meta__ as Record<string, unknown>;
  return meta.settings as
    | {
        code_requirements?: string;
        code_index_url?: string;
        code_libraries?: string[];
        timeouts?: Record<string, number>;
        egress?: { allowed_hosts: string[] };
      }
    | undefined;
};

const save = () => fireEvent.click(document.querySelector('[data-action="settings-save"]')!);

beforeEach(() => {
  useWorkflowEditStore.getState().setDraft({ node_1: { node_id: 'node_1' }, __meta__: {} });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('WorkflowSettingsModal', () => {
  it('renders the incremental Python requirements configuration', () => {
    renderModal();

    expect(screen.getByTestId('settings-tab-code')).toBeInTheDocument();
    expect(screen.getByTestId('settings-code-requirements')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-code-index-url')).not.toBeInTheDocument();
  });

  it('saves and restores code_requirements', () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal('fetch', fetchSpy);
    renderModal();
    fireEvent.change(screen.getByTestId('settings-code-requirements'), {
      target: { value: '  pandas==2.2.0\nopenpyxl==3.1.5  ' },
    });

    save();

    expect(settingsOf()?.code_requirements).toBe('pandas==2.2.0\nopenpyxl==3.1.5');
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('settings-code-env-status')).toHaveTextContent(
      'Packages are installed when a new sandbox is initialized',
    );
  });

  it('round-trips set timeouts + omits empty ones', () => {
    renderModal();
    fireEvent.change(screen.getByTestId('settings-timeout-workflow'), {
      target: { value: '7200' },
    });
    fireEvent.change(screen.getByTestId('settings-timeout-code'), { target: { value: '120' } });

    save();

    expect(settingsOf()?.timeouts).toEqual({ workflow: 7200, code: 120 });
    expect(settingsOf()?.timeouts).not.toHaveProperty('http');
  });

  it('omits the timeouts object entirely when none are set', () => {
    renderModal();

    save();

    expect(settingsOf()).toEqual({});
    expect(settingsOf()).not.toHaveProperty('timeouts');
  });

  it('treats 0 as "use default" and omits it', () => {
    renderModal();
    fireEvent.change(screen.getByTestId('settings-timeout-code'), { target: { value: '0' } });

    save();

    expect(settingsOf()).not.toHaveProperty('timeouts');
  });

  it('seeds timeout inputs from existing settings', () => {
    useWorkflowEditStore.getState().setDraft({
      __meta__: { settings: { timeouts: { workflow: 1800, http: 15 } } },
    });

    renderModal();

    expect((screen.getByTestId('settings-timeout-workflow') as HTMLInputElement).value).toBe(
      '1800',
    );
    expect((screen.getByTestId('settings-timeout-http') as HTMLInputElement).value).toBe('15');
    expect((screen.getByTestId('settings-timeout-code') as HTMLInputElement).value).toBe('');
  });

  it('trims, dedupes, and saves network egress hosts', () => {
    renderModal();
    fireEvent.change(screen.getByTestId('settings-egress-hosts'), {
      target: { value: '  a.test  \n\nb.test\na.test\n   \n' },
    });

    save();

    expect(settingsOf()?.egress?.allowed_hosts).toEqual(['a.test', 'b.test']);
  });

  it('seeds network egress hosts from existing settings', () => {
    useWorkflowEditStore.getState().setDraft({
      __meta__: { settings: { egress: { allowed_hosts: ['a.test'] } } },
    });

    renderModal();

    expect((screen.getByTestId('settings-egress-hosts') as HTMLTextAreaElement).value).toBe(
      'a.test',
    );
  });

  it('preserves requirements and sibling settings but deletes retired Code settings', () => {
    useWorkflowEditStore.getState().setDraft({
      __meta__: {
        settings: {
          display: { theme: 'compact' },
          agent_tools: { mcp_server_ids: ['x'] },
          code_requirements: 'pandas',
          code_index_url: 'https://mirror/simple',
          code_libraries: ['numpy'],
        },
      },
    });
    renderModal();

    save();

    expect(settingsOf()).toHaveProperty('display');
    expect(settingsOf()).not.toHaveProperty('agent_tools');
    expect(settingsOf()?.code_requirements).toBe('pandas');
    expect(settingsOf()).not.toHaveProperty('code_index_url');
    expect(settingsOf()).not.toHaveProperty('code_libraries');
  });

  it('does not apply edits on cancel', () => {
    const onOpenChange = vi.fn();
    renderModal(onOpenChange);
    fireEvent.change(screen.getByTestId('settings-timeout-code'), { target: { value: '120' } });

    fireEvent.click(document.querySelector('[data-action="settings-cancel"]')!);

    expect(useWorkflowEditStore.getState().undoStack.length).toBe(0);
    expect(useWorkflowEditStore.getState().dirty).toBe(false);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
