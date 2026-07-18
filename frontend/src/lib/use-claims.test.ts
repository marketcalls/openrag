import { act, renderHook } from '@testing-library/react';

import { setAccessToken } from './auth-store';
import { useClaims } from './use-claims';

const base64 = (value: object) =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const token = `${base64({ alg: 'HS256' })}.${base64({
  sub: 'u1',
  org: 'o1',
  role: 'superadmin',
  exp: 9,
})}.signature`;

afterEach(() => act(() => setAccessToken(null)));

test('exposes decoded claims and tracks token changes', () => {
  const { result } = renderHook(() => useClaims());
  expect(result.current).toBeNull();
  act(() => setAccessToken(token));
  expect(result.current?.role).toBe('superadmin');
});
