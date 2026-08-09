import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router';
import { ErrorBoundary } from '@/app/ErrorBoundary';
import { Providers } from '@/app/providers';
import { router } from '@/app/router';
// Side-effect import: initialises i18next BEFORE the React tree mounts so
// route components calling `useTranslation()` always find a ready instance.
import '@/lib/i18n';
import { initAuthExtensionSync } from '@/lib/auth-extension-sync';
import { installSessionFetch } from '@/lib/api/session-fetch';
import './index.css';

// Install the cookie/CSRF transport before any component or query can issue a
// request. Only platform API URLs are modified; third-party fetches are intact.
installSessionFetch();

// Share a one-time Session exchange code with the MV3 extension. The primary
// HttpOnly Web Session itself never enters JavaScript or chrome.storage.
initAuthExtensionSync();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary scope="global">
      <Providers>
        <RouterProvider router={router} />
      </Providers>
    </ErrorBoundary>
  </StrictMode>,
);
