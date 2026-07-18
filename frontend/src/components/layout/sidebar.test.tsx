import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

import { WorkspaceProvider } from '@/features/workspaces/workspace-context';
import { setAccessToken } from '@/lib/auth-store';

import { Sidebar } from './sidebar';

const base64 = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const tokenFor = (role: string) =>
  `${base64({ alg: 'HS256' })}.${base64({
    sub: 'u1',
    org: 'o1',
    role,
    exp: 9,
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
  setAccessToken(tokenFor('user'));
  renderSidebar();
  expect(await screen.findByText('Finance')).toBeInTheDocument();
  expect(screen.queryByText('Users')).not.toBeInTheDocument();
  expect(screen.queryByText('Models')).not.toBeInTheDocument();
});

test('a superadmin sees user and model administration links', async () => {
  setAccessToken(tokenFor('superadmin'));
  renderSidebar();
  expect(await screen.findByText('Finance')).toBeInTheDocument();
  expect(await screen.findByText('Users')).toBeInTheDocument();
  expect(screen.getByText('Models')).toBeInTheDocument();
});
