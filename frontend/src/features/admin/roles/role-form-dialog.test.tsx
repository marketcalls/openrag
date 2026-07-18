import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { RoleOut } from '@/api/types';
import { useUsers } from '@/features/admin/users/queries';

import { RoleFormDialog } from './role-form-dialog';

const catalog = [
  {
    code: 'document.read',
    label: 'Read documents',
    group: 'Knowledge',
    description: 'View authorized documents.',
  },
  {
    code: 'role.manage',
    label: 'Manage roles',
    group: 'Administration',
    description: 'Create roles and bindings.',
  },
];

function renderDialog(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RoleFormDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('creates a custom role using only permissions from the server catalog', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.method === 'GET') return Response.json(catalog);
    return Response.json(
      {
        id: '550e8400-e29b-41d4-a716-446655440010',
        key: 'custom_1',
        name: 'Safety reviewer',
        description: 'Reviews approved safety evidence.',
        permissions: ['document.read'],
        is_system: false,
        is_assignable: true,
      },
      { status: 201 },
    );
  });
  const user = userEvent.setup();
  renderDialog(fetchMock);

  await screen.findByRole('checkbox', { name: /Read documents/ });
  expect(screen.queryByText(/platform superadmin/i)).not.toBeInTheDocument();
  await user.type(screen.getByLabelText('Role name'), 'Safety reviewer');
  await user.type(screen.getByLabelText('Description'), 'Reviews approved safety evidence.');
  await user.click(screen.getByRole('checkbox', { name: /Read documents/ }));
  await user.click(screen.getByRole('button', { name: 'Create role' }));

  await waitFor(() => expect(requests.some((request) => request.method === 'POST')).toBe(true));
  const post = requests.find((request) => request.method === 'POST');
  if (!post) throw new Error('Expected role POST');
  expect(await post.clone().json()).toEqual({
    name: 'Safety reviewer',
    description: 'Reviews approved safety evidence.',
    permissions: ['document.read'],
  });
});

test('keeps the form state and reports a server authorization error', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    if (input.method === 'GET') return Response.json(catalog);
    return Response.json(
      { detail: 'Your role-management permission was revoked.' },
      { status: 403, headers: { 'content-type': 'application/problem+json' } },
    );
  });
  const user = userEvent.setup();
  renderDialog(fetchMock);

  await screen.findByRole('checkbox', { name: /Read documents/ });
  await user.type(screen.getByLabelText('Role name'), 'Temporary reviewer');
  await user.click(screen.getByRole('button', { name: 'Create role' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Your role-management permission was revoked.',
  );
  expect(screen.getByLabelText('Role name')).toHaveValue('Temporary reviewer');
});

test('refreshes embedded user role chips after a successful role patch', async () => {
  const editableRole: RoleOut = {
    id: '550e8400-e29b-41d4-a716-446655440011',
    key: 'custom_reviewer',
    name: 'Safety reviewer',
    description: 'Reviews evidence.',
    permissions: ['document.read'],
    is_system: false,
    is_assignable: true,
  };
  let userReads = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    if (input.method === 'GET' && input.url.endsWith('/api/v1/roles/catalog')) {
      return Response.json(catalog);
    }
    if (input.method === 'GET' && input.url.endsWith('/api/v1/users')) {
      userReads += 1;
      const role =
        userReads === 1 ? editableRole : { ...editableRole, name: 'Senior safety reviewer' };
      return Response.json([
        {
          id: '550e8400-e29b-41d4-a716-446655440012',
          email: 'reviewer@acme.com',
          active: true,
          is_platform_superadmin: false,
          roles: [role],
        },
      ]);
    }
    if (input.method === 'PATCH') {
      return Response.json({ ...editableRole, name: 'Senior safety reviewer' });
    }
    return Response.json([]);
  });
  vi.stubGlobal('fetch', fetchMock);
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  function RoleChipProbe() {
    const users = useUsers();
    return <p>{users.data?.[0]?.roles[0]?.name ?? 'Loading user role…'}</p>;
  }

  render(
    <QueryClientProvider client={queryClient}>
      <RoleChipProbe />
      <RoleFormDialog open role={editableRole} onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();

  expect(await screen.findByText('Safety reviewer')).toBeInTheDocument();
  await screen.findByRole('checkbox', { name: /Read documents/ });
  await user.clear(screen.getByLabelText('Role name'));
  await user.type(screen.getByLabelText('Role name'), 'Senior safety reviewer');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));

  expect(await screen.findByText('Senior safety reviewer')).toBeInTheDocument();
  expect(userReads).toBe(2);
});
