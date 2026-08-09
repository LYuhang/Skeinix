/**
 * `CredentialsPage` and `CredentialRow` smoke tests.
 *
 * Mocks the API client module (`@/lib/api/llm-credentials`) so the REAL
 * react-query hooks run end-to-end. Asserts:
 *   - the list renders one row per credential (public fields)
 *   - the api_key remains permanently masked
 *   - no reveal/copy control exists because provider secrets are write-only
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { I18nextProvider, initReactI18next } from 'react-i18next';
import i18n from 'i18next';

vi.mock('@/lib/api/llm-credentials', () => ({
  listLlmCredentials: vi.fn(async () => [
    {
      id: 'cred-1',
      name: 'My OpenAI key',
      description: 'prod',
      provider: 'OpenAI',
      model_context_tokens: 128000,
      created_at: '2026-06-01T00:00:00Z',
      updated_at: '2026-06-02T00:00:00Z',
    },
  ]),
  getLlmCredential: vi.fn(),
  createLlmCredential: vi.fn(),
  updateLlmCredential: vi.fn(),
  deleteLlmCredential: vi.fn(),
}));

// sonner toast is a side effect we don't assert on here.
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { CredentialsPage } from '@/pages/credentials/CredentialsPage';

void i18n.use(initReactI18next).init({
  lng: 'en',
  resources: { en: { translation: {} } },
  interpolation: { escapeValue: false },
});

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <CredentialsPage />
        </MemoryRouter>
      </QueryClientProvider>
    </I18nextProvider>,
  );
}

describe('CredentialsPage', () => {
  it('renders a permanently masked write-only credential row', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('My OpenAI key')).toBeInTheDocument(),
    );
    // Public fields present.
    expect(screen.getByText('OpenAI')).toBeInTheDocument();
    // Secret material is neither returned nor exposed through a UI control.
    expect(screen.getByText('••••••••')).toBeInTheDocument();
    expect(screen.getByText('Stored · write-only')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reveal key' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Copy key' })).not.toBeInTheDocument();
  });
});
