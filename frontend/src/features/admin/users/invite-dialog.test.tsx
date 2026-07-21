import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { InviteDialog } from './invite-dialog';

const USER_ROLE_ID = '550e8400-e29b-41d4-a716-446655440001';
const roles = [
  {
    id: USER_ROLE_ID,
    key: 'user',
    name: 'User',
    description: 'Grounded chat access',
    permissions: ['chat.use', 'document.read'],
    is_system: true,
    is_assignable: true,
  },
  {
    id: '550e8400-e29b-41d4-a716-446655440002',
    key: 'platform_superadmin',
    name: 'Platform Superadmin',
    description: 'Must never be assignable',
    permissions: [],
    is_system: true,
    is_assignable: false,
  },
];

function renderDialog(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <InviteDialog open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('submits an opaque role id and shows a one-time copyable invite link', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.method === 'GET' && input.url.endsWith('/api/v1/roles')) {
      return Response.json(roles);
    }
    return Response.json(
      { accepted: true, accept_path: '/accept-invite?token=demo-once' },
      { status: 202 },
    );
  });
  const user = userEvent.setup();
  const writeText = vi.spyOn(navigator.clipboard, 'writeText');
  renderDialog(fetchMock);

  await screen.findByRole('option', { name: 'User' });
  expect(screen.queryByRole('option', { name: 'Platform Superadmin' })).not.toBeInTheDocument();
  await user.type(screen.getByLabelText('Email'), 'new@acme.com');
  await user.selectOptions(screen.getByLabelText('Role'), USER_ROLE_ID);
  await user.click(screen.getByRole('button', { name: 'Create invite link' }));

  expect(await screen.findByText('Invitation ready')).toBeInTheDocument();
  const link = screen.getByLabelText('One-time invite link');
  expect(link).toHaveValue('http://localhost:3000/accept-invite?token=demo-once');
  await user.click(screen.getByRole('button', { name: 'Copy invite link' }));
  expect(writeText).toHaveBeenCalledWith(
    'http://localhost:3000/accept-invite?token=demo-once',
  );
  await waitFor(() => expect(requests.some((request) => request.method === 'POST')).toBe(true));
  const post = requests.find((request) => request.method === 'POST');
  if (!post) throw new Error('Expected invitation POST');
  expect(await post.clone().json()).toEqual({ email: 'new@acme.com', role_id: USER_ROLE_ID });
});

test('renders an authorization error and keeps the invitation form', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    if (input.method === 'GET') return Response.json(roles);
    return Response.json(
      { detail: 'You no longer have permission to invite users.' },
      { status: 403, headers: { 'content-type': 'application/problem+json' } },
    );
  });
  const user = userEvent.setup();
  renderDialog(fetchMock);

  await screen.findByRole('option', { name: 'User' });
  await user.type(screen.getByLabelText('Email'), 'new@acme.com');
  await user.click(screen.getByRole('button', { name: 'Create invite link' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'You no longer have permission to invite users.',
  );
  expect(screen.getByLabelText('Email')).toHaveValue('new@acme.com');
});
