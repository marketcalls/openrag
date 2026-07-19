import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { DocumentOut } from '@/api/types';
import { Table, TBody } from '@/components/ui/table';

import { DocumentRow } from './document-row';

function failedDocument(): DocumentOut {
  return {
    id: '10000000-0000-0000-0000-000000000001',
    filename: 'invoice.pdf',
    mime: 'application/pdf',
    size_bytes: 2048,
    status: 'failed',
    page_count: null,
    error_code: 'processing_failed',
    created_at: '2026-07-19T12:00:00Z',
  };
}

test('shows only the safe document processing error code', async () => {
  const user = userEvent.setup();
  render(
    <Table>
      <TBody>
        <DocumentRow document={failedDocument()} deleting={false} onDelete={() => undefined} />
      </TBody>
    </Table>,
  );

  await user.click(screen.getByRole('button', { name: 'Show failure reason' }));

  expect(await screen.findByText('processing_failed')).toBeInTheDocument();
  expect(screen.queryByText(/traceback|password=/i)).not.toBeInTheDocument();
});
