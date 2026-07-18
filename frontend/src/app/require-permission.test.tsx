import { render, screen } from '@testing-library/react';
import { act } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { setAccessToken } from '@/lib/auth-store';

import { RequirePermission, RequirePlatformSuperadmin } from './require-permission';

const USER_ID = '550e8400-e29b-41d4-a716-446655440000';
const ORG_ID = '6ba7b810-9dad-41d1-80b4-00c04fd430c8';
const base64 = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const tokenFor = (permissions: unknown, platformSuperadmin = false) =>
  `${base64({ alg: 'HS256' })}.${base64({
    sub: USER_ID,
    org: ORG_ID,
    platform_superadmin: platformSuperadmin,
    permissions,
    exp: 4_102_444_800,
  })}.signature`;

function renderGuard() {
  render(
    <MemoryRouter
      initialEntries={['/admin/roles']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/chat" element={<p>Safe chat</p>} />
        <Route element={<RequirePermission permission="role.manage" />}>
          <Route path="/admin/roles" element={<p>Role administration</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => act(() => setAccessToken(null)));

test('denies a route when the claim lacks the permission', () => {
  act(() => setAccessToken(tokenFor(['document.read'])));
  renderGuard();
  expect(screen.getByText('Safe chat')).toBeInTheDocument();
  expect(screen.queryByText('Role administration')).not.toBeInTheDocument();
});

test('allows platform superadmin without an organization permission', () => {
  act(() => setAccessToken(tokenFor([], true)));
  renderGuard();
  expect(screen.getByText('Role administration')).toBeInTheDocument();
});

test('denies platform routes even when an organization claim has model configuration', () => {
  act(() => setAccessToken(tokenFor(['model.configure'])));
  render(
    <MemoryRouter
      initialEntries={['/admin/models']}
      future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
    >
      <Routes>
        <Route path="/chat" element={<p>Safe chat</p>} />
        <Route element={<RequirePlatformSuperadmin />}>
          <Route path="/admin/models" element={<p>Platform models</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
  expect(screen.getByText('Safe chat')).toBeInTheDocument();
  expect(screen.queryByText('Platform models')).not.toBeInTheDocument();
});
