import { act, renderHook } from '@testing-library/react';

import { setAccessToken } from './auth-store';
import { useClaims } from './use-claims';

const base64 = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const token = `${base64({ alg: 'HS256' })}.${base64({
  sub: '550e8400-e29b-41d4-a716-446655440000',
  org: '6ba7b810-9dad-41d1-80b4-00c04fd430c8',
  platform_superadmin: true,
  permissions: [],
  exp: 4_102_444_800,
})}.signature`;

afterEach(() => act(() => setAccessToken(null)));

test('exposes decoded claims and tracks token changes', () => {
  const { result } = renderHook(() => useClaims());
  expect(result.current).toBeNull();
  act(() => setAccessToken(token));
  expect(result.current?.platform_superadmin).toBe(true);
});
