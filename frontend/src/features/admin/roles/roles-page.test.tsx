import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';

import { RolesPage } from './roles-page';

const roles = [
  {
    id: '550e8400-e29b-41d4-a716-446655440020',
    key: 'administrator',
    name: 'Administrator',
    description: 'Organization administration',
    permissions: [
      'audit.read',
      'chat.use',
      'document.approve',
      'document.read',
      'document.upload',
      'model.configure',
      'rag.evaluate',
      'role.manage',
      'user.manage',
      'workspace.manage',
      'workspace.read_all',
    ],
    is_system: true,
    is_assignable: true,
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440021',
    key: 'custom_a',
    name: 'Safety reviewer',
    description: 'Reviews controlled knowledge',
    permissions: ['document.read', 'document.approve'],
    is_system: false,
    is_assignable: true,
  },
];

function renderPage(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RolesPage />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('lists built-in and custom roles with protected signals and permission counts', async () => {
  renderPage(vi.fn(async () => Response.json(roles)));

  expect(await screen.findByText('Administrator')).toBeInTheDocument();
  expect(screen.getByText('Safety reviewer')).toBeInTheDocument();
  expect(screen.getByText('11 permissions')).toBeInTheDocument();
  expect(screen.getByText('2 permissions')).toBeInTheDocument();
  expect(screen.getAllByText('Protected')).toHaveLength(2);
  expect(screen.queryByText(/platform superadmin/i)).not.toBeInTheDocument();
});

test('shows a useful empty state when no roles are returned', async () => {
  renderPage(vi.fn(async () => Response.json([])));
  expect(await screen.findByText('No roles are available')).toBeInTheDocument();
});
