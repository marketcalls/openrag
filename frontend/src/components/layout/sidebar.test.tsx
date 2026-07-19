import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { WorkspaceProvider } from '@/features/workspaces/workspace-context';
import { setAccessToken } from '@/lib/auth-store';

import { Sidebar } from './sidebar';

const base64 = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const tokenFor = (permissions: string[], platformSuperadmin = false) =>
  `${base64({ alg: 'HS256' })}.${base64({
    sub: '550e8400-e29b-41d4-a716-446655440000',
    org: '6ba7b810-9dad-41d1-80b4-00c04fd430c8',
    platform_superadmin: platformSuperadmin,
    permissions,
    exp: 4_102_444_800,
  })}.signature`;

function renderSidebar() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      const body = request.url.includes('/workspaces')
        ? [
            {
              id: 'w1',
              name: 'Finance',
              embedding_model: 'bge-m3',
              min_score: 0.35,
              default_model_id: null,
            },
          ]
        : [];
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <MemoryRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
        <WorkspaceProvider>
          <Sidebar />
        </WorkspaceProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  act(() => setAccessToken(null));
  localStorage.clear();
});

test('a user sees no administration links', async () => {
  setAccessToken(tokenFor(['chat.use', 'document.read']));
  renderSidebar();
  expect(await screen.findByText('Finance')).toBeInTheDocument();
  expect(screen.queryByText('Users')).not.toBeInTheDocument();
  expect(screen.queryByText('Models')).not.toBeInTheDocument();
  expect(screen.queryByText('Embeddings')).not.toBeInTheDocument();
  expect(screen.queryByText('Roles')).not.toBeInTheDocument();
});

test('capability hints show only matching organization administration links', async () => {
  setAccessToken(tokenFor(['user.manage', 'role.manage']));
  renderSidebar();
  expect(await screen.findByText('Finance')).toBeInTheDocument();
  expect(screen.getByText('Users')).toBeInTheDocument();
  expect(screen.getByText('Roles')).toBeInTheDocument();
  expect(screen.queryByText('Models')).not.toBeInTheDocument();
  expect(screen.queryByText('Embeddings')).not.toBeInTheDocument();
});

test('a platform superadmin sees platform and organization administration links', async () => {
  setAccessToken(tokenFor([], true));
  renderSidebar();
  expect(await screen.findByText('Finance')).toBeInTheDocument();
  expect(await screen.findByText('Users')).toBeInTheDocument();
  expect(screen.getByText('Models')).toBeInTheDocument();
  expect(screen.getByText('Embeddings')).toBeInTheDocument();
  expect(screen.getByText('Roles')).toBeInTheDocument();
});
