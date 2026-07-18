import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { setAccessToken } from '@/lib/auth-store';

import { RequireAuth } from './require-auth';

function renderProtected() {
  return render(
    <MemoryRouter
      initialEntries={['/secret']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/login" element={<div>login page</div>} />
        <Route element={<RequireAuth />}>
          <Route path="/secret" element={<div>secret page</div>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('renders children when an access token exists', () => {
  setAccessToken('tok');
  renderProtected();
  expect(screen.getByText('secret page')).toBeInTheDocument();
});

test('restores the session through the refresh cookie when no token exists', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ access_token: 'restored' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  renderProtected();
  expect(await screen.findByText('secret page')).toBeInTheDocument();
});

test('redirects to login when refresh fails', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
  renderProtected();
  expect(await screen.findByText('login page')).toBeInTheDocument();
});
