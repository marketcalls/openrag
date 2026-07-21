import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { SidebarChatList } from './sidebar-chat-list';

const { mutate } = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock('@/features/chat/queries', () => ({
  useChatSearch: () => ({
    data: {
      pages: [
        {
          items: [
            {
              id: 'chat-1',
              title: 'Quarterly report',
              workspace_id: 'workspace-1',
            },
          ],
        },
      ],
    },
    isPending: false,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: vi.fn(),
  }),
  useDeleteChat: () => ({ mutate, isPending: false }),
}));

vi.mock('@/features/workspaces/workspace-context', () => ({
  useWorkspace: () => ({ workspaceId: 'workspace-1' }),
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

beforeEach(() => {
  mutate.mockReset();
});

test('deletes a chat without opening a native confirmation alert', async () => {
  const confirm = vi.spyOn(window, 'confirm');
  const user = userEvent.setup();
  render(
    <MemoryRouter
      initialEntries={['/chat/chat-1']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <SidebarChatList />
    </MemoryRouter>,
  );

  await user.click(screen.getByRole('button', { name: 'Delete Quarterly report' }));

  expect(confirm).not.toHaveBeenCalled();
  expect(mutate).toHaveBeenCalledWith(
    'chat-1',
    expect.objectContaining({
      onSuccess: expect.any(Function),
      onError: expect.any(Function),
    }),
  );
  confirm.mockRestore();
});
