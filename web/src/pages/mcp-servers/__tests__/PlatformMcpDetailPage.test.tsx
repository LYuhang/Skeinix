import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import i18n from 'i18next';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

const tools = Array.from({ length: 19 }, (_, index) => ({
  name: `browser_tool_${index + 1}`,
  description: `Browser capability ${index + 1}`,
  input_schema: {
    type: 'object',
    properties: { url: { type: 'string' } },
    additionalProperties: false,
  },
  annotations: { readOnlyHint: index % 2 === 0 },
}));

vi.mock('@/lib/api/queries/mcp-servers', () => ({
  usePlatformMcpServices: () => ({
    data: [{
      id: 'browser',
      name: 'Browser',
      description: 'Browser platform tools',
      activation: '/browser',
      activation_mode: 'command',
      runtime_types: ['langchain', 'codex'],
      tools,
    }],
    isLoading: false,
    isError: false,
  }),
}));

import { PlatformMcpDetailPage } from '@/pages/mcp-servers/PlatformMcpDetailPage';

const testI18n = i18n.createInstance();
void testI18n.use(initReactI18next).init({
  lng: 'en',
  fallbackLng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

describe('PlatformMcpDetailPage', () => {
  it('shows a searchable tool directory with list and details panes', async () => {
    const user = userEvent.setup();
    render(
      <I18nextProvider i18n={testI18n}>
        <MemoryRouter initialEntries={['/mcp-servers/platform/browser']}>
          <Routes>
            <Route path="/mcp-servers/platform/:platformId" element={<PlatformMcpDetailPage />} />
          </Routes>
        </MemoryRouter>
      </I18nextProvider>,
    );

    expect(screen.getByRole('heading', { name: 'Browser' })).toBeInTheDocument();
    expect(screen.queryByText('browser_tool_19')).toBeNull();

    await user.click(screen.getByTestId('platform-mcp-tools-tab'));
    expect(screen.getAllByText('browser_tool_1').length).toBeGreaterThan(0);
    expect(screen.getAllByText('browser_tool_19').length).toBeGreaterThan(0);
    expect(screen.getByTestId('mcp-tool-directory')).toHaveClass('md:grid-cols-[minmax(220px,300px)_minmax(0,1fr)]');
    expect(screen.getByText('Input parameters')).toBeInTheDocument();

    await user.type(screen.getByPlaceholderText('Search tools'), 'tool_19');
    expect(screen.queryByText('browser_tool_1')).toBeNull();
    expect(screen.getAllByText('browser_tool_19').length).toBeGreaterThan(0);
  });
});
