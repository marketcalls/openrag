import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

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
