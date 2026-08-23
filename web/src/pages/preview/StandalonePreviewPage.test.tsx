import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import { StandalonePreviewPage } from '@/pages/preview/StandalonePreviewPage';

vi.mock('@/pages/chat/preview/ChatFilePreview', () => ({
  ChatFilePreview: ({
    fileRef,
    fileType,
    allowOpenInNewPage,
  }: {
    fileRef: { path: string; chatId?: string };
    fileType: string;
    allowOpenInNewPage?: boolean;
  }) => (
    <div data-testid="shared-preview">
      {fileRef.chatId}:{fileRef.path}:{fileType}:{String(allowOpenInNewPage)}
    </div>
  ),
}));

describe('StandalonePreviewPage', () => {
  it('reuses the shared file Preview for a validated URL', () => {
    render(
      <MemoryRouter initialEntries={[
        '/preview?scope=chat&chatId=chat-1&path=%2Fdata%2Fbrief.docx&fileType=docx',
      ]}>
        <StandalonePreviewPage />
      </MemoryRouter>,
    );

    expect(screen.getByRole('heading', { name: 'brief.docx' })).toBeInTheDocument();
    expect(screen.getByTestId('shared-preview')).toHaveTextContent(
      'chat-1:/data/brief.docx:docx:false',
    );
  });

  it('does not call the Preview renderer for an invalid file coordinate', () => {
    render(
      <MemoryRouter initialEntries={[
        '/preview?scope=chat&chatId=chat-1&path=%2Fetc%2Fpasswd',
      ]}>
        <StandalonePreviewPage />
      </MemoryRouter>,
    );

    expect(screen.getByText('Unable to open Preview')).toBeInTheDocument();
    expect(screen.queryByTestId('shared-preview')).not.toBeInTheDocument();
  });
});
