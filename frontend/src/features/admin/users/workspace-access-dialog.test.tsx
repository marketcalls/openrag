import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { UserOut } from '@/api/types';

import { WorkspaceAccessDialog } from './workspace-access-dialog';

const userRecord: UserOut = {
  id: 'user-1',
  email: 'new@acme.com',
  active: true,
  is_platform_superadmin: false,
  roles: [],
};

function workspaceResponse() {
  return new Response(
    JSON.stringify([
      {
        id: 'workspace-1',
        name: 'Finance',
        embedding_model: 'bge-m3',
        min_score: 0.35,
        default_model_id: null,
      },
    ]),
    { status: 200, headers: { 'content-type': 'application/json' } },
  );
}

function renderDialog(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <WorkspaceAccessDialog user={userRecord} open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('grants a user access to the selected workspace', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.url.endsWith('/api/v1/workspaces')) return workspaceResponse();
    if (input.method === 'GET' && input.url.endsWith('/members')) {
      return new Response('[]', {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }
    return new Response(null, { status: 204 });
  });
  const user = userEvent.setup();
  renderDialog(fetchMock);

  await screen.findByRole('option', { name: 'Finance' });
  await user.selectOptions(screen.getByLabelText('Workspace'), 'workspace-1');
  await user.click(await screen.findByRole('button', { name: 'Grant access' }));

  await waitFor(() =>
    expect(requests.some((request) => request.method === 'POST')).toBe(true),
  );
  const post = requests.find((request) => request.method === 'POST');
  if (!post) throw new Error('Expected membership POST');
  expect(await post.clone().json()).toEqual({ user_id: 'user-1' });
});

test('shows when the user is already a member', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    if (input.url.endsWith('/api/v1/workspaces')) return workspaceResponse();
    return new Response(
      JSON.stringify([{ user_id: 'user-1', email: 'new@acme.com' }]),
      { status: 200, headers: { 'content-type': 'application/json' } },
    );
  });
  const user = userEvent.setup();
  renderDialog(fetchMock);

  await screen.findByRole('option', { name: 'Finance' });
  await user.selectOptions(screen.getByLabelText('Workspace'), 'workspace-1');

  expect(await screen.findByText('Already a member')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Grant access' })).not.toBeInTheDocument();
});
