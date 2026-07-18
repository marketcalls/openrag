import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { setAccessToken } from '@/lib/auth-store';

import { UsersPage } from './users-page';

const ENGINEER_ID = '550e8400-e29b-41d4-a716-446655440031';
const REVIEWER_ID = '550e8400-e29b-41d4-a716-446655440032';
const USER_ID = '550e8400-e29b-41d4-a716-446655440030';
const roles = [
  {
    id: ENGINEER_ID,
    key: 'engineer',
    name: 'Engineer',
    description: 'Contribute knowledge',
    permissions: ['chat.use', 'document.read', 'document.upload'],
    is_system: true,
    is_assignable: true,
  },
  {
    id: REVIEWER_ID,
    key: 'custom_reviewer',
    name: 'Safety reviewer',
    description: 'Review evidence',
    permissions: ['document.read', 'document.approve'],
    is_system: false,
    is_assignable: true,
  },
];
const users = [
  {
    id: USER_ID,
    email: 'engineer@acme.com',
    active: true,
    is_platform_superadmin: false,
    roles: [roles[0]],
  },
];

const base64 = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const tokenFor = (permissions: string[]) =>
  `${base64({ alg: 'HS256' })}.${base64({
    sub: '550e8400-e29b-41d4-a716-446655440040',
    org: '6ba7b810-9dad-41d1-80b4-00c04fd430c8',
    platform_superadmin: false,
    permissions,
    exp: 4_102_444_800,
  })}.signature`;

function renderPage(
  fetchMock: ReturnType<typeof vi.fn>,
  permissions = ['user.manage', 'role.manage'],
) {
  act(() => setAccessToken(tokenFor(permissions)));
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <UsersPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => setAccessToken(null));
});

test('shows effective bindings and replaces them with opaque role ids', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.method === 'GET' && input.url.endsWith('/api/v1/users')) {
      return Response.json(users);
    }
    if (input.method === 'GET' && input.url.endsWith('/api/v1/roles')) {
      return Response.json(roles);
    }
    return Response.json({ ...users[0], roles: [roles[1]] });
  });
  const user = userEvent.setup();
  renderPage(fetchMock);

  expect(await screen.findByText('Engineer')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Manage roles for engineer@acme.com' }));
  await user.click(screen.getByRole('checkbox', { name: /Engineer/ }));
  await user.click(screen.getByRole('checkbox', { name: /Safety reviewer/ }));
  await user.click(screen.getByRole('button', { name: 'Save roles' }));

  await waitFor(() => expect(requests.some((request) => request.method === 'PUT')).toBe(true));
  const put = requests.find((request) => request.method === 'PUT');
  if (!put) throw new Error('Expected role binding PUT');
  expect(await put.clone().json()).toEqual({ role_ids: [REVIEWER_ID] });
});

test('keeps effective roles unchanged when authorization fails', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    if (input.method === 'GET' && input.url.endsWith('/api/v1/users')) {
      return Response.json(users);
    }
    if (input.method === 'GET') return Response.json(roles);
    return Response.json(
      { detail: 'Role management permission was revoked.' },
      { status: 403, headers: { 'content-type': 'application/problem+json' } },
    );
  });
  const user = userEvent.setup();
  renderPage(fetchMock);

  expect(await screen.findByText('Engineer')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Manage roles for engineer@acme.com' }));
  await user.click(screen.getByRole('checkbox', { name: /Safety reviewer/ }));
  await user.click(screen.getByRole('button', { name: 'Save roles' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Role management permission was revoked.',
  );
  expect(screen.getByRole('checkbox', { name: /Engineer/ })).toBeChecked();
  expect(screen.getByRole('checkbox', { name: /Safety reviewer/ })).not.toBeChecked();
  expect(screen.getByText('Engineer')).toBeInTheDocument();
});

test('does not expose role-dependent controls without role.manage', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    return Response.json(users);
  });
  renderPage(fetchMock, ['user.manage']);

  expect(await screen.findByText('engineer@acme.com')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Manage roles for/ })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Invite' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Workspace access' })).not.toBeInTheDocument();
});

test('workspace.manage independently enables workspace access controls', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    return Response.json(users);
  });
  renderPage(fetchMock, ['user.manage', 'workspace.manage']);

  expect(await screen.findByText('engineer@acme.com')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Workspace access' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /Manage roles for/ })).not.toBeInTheDocument();
});
