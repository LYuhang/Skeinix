import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { AgentSettingsModal } from '@/components/agent-sidebar/AgentSettingsModal';

function makeClient() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity, gcTime: Infinity },
    },
  });
  client.setQueryData(['llm-credentials', 'list'], []);
  return client;
}

function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={makeClient()}>{children}</QueryClientProvider>;
}

describe('AgentSettingsModal', () => {
  it('renders model settings only', () => {
    render(<AgentSettingsModal open onOpenChange={() => {}} />, { wrapper: Wrapper });
    expect(screen.getByTestId('agent-settings-temperature')).toBeInTheDocument();
    expect(screen.queryByTestId('agent-settings-tab-mcp')).not.toBeInTheDocument();
    expect(screen.queryByTestId('agent-settings-tab-skills')).not.toBeInTheDocument();
  });

  it('keeps modelOnly call sites compatible', () => {
    render(<AgentSettingsModal open onOpenChange={() => {}} modelOnly />, { wrapper: Wrapper });
    expect(screen.getByTestId('agent-settings-temperature')).toBeInTheDocument();
  });
});
