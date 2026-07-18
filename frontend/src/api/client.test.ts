import { setAccessToken } from '@/lib/auth-store';

import { authFetch, refreshAccessToken, setOnAuthFailure } from './client';

function response(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
  setOnAuthFailure(() => undefined);
});

test('attaches the bearer token', async () => {
  setAccessToken('tok-1');
  const fetchMock = vi.fn(async (request: Request) => {
    expect(request.headers.get('authorization')).toBe('Bearer tok-1');
    return response(200);
  });
  vi.stubGlobal('fetch', fetchMock);

  const result = await authFetch(new Request('http://x/api/v1/workspaces'));

  expect(result.status).toBe(200);
});

test('concurrent 401 responses share one refresh and retry with the new token', async () => {
  setAccessToken('stale');
  let refreshCalls = 0;
  const fetchMock = vi.fn(async (request: RequestInfo | URL) => {
    const url = typeof request === 'string' ? request : request instanceof URL ? request.href : request.url;
    if (url.includes('/auth/refresh')) {
      refreshCalls += 1;
      await new Promise((resolve) => setTimeout(resolve, 10));
      return response(200, { access_token: 'fresh' });
    }
    return request instanceof Request && request.headers.get('authorization') === 'Bearer fresh'
      ? response(200)
      : response(401);
  });
  vi.stubGlobal('fetch', fetchMock);

  const [first, second] = await Promise.all([
    authFetch(new Request('http://x/api/v1/workspaces')),
    authFetch(new Request('http://x/api/v1/users')),
  ]);

  expect(first.status).toBe(200);
  expect(second.status).toBe(200);
  expect(refreshCalls).toBe(1);
});

test('refresh failure clears the token and fires the auth failure callback', async () => {
  setAccessToken('stale');
  const onFailure = vi.fn();
  setOnAuthFailure(onFailure);
  vi.stubGlobal('fetch', vi.fn(async () => response(401)));

  const result = await authFetch(new Request('http://x/api/v1/workspaces'));

  expect(result.status).toBe(401);
  expect(onFailure).toHaveBeenCalledOnce();
  expect(await refreshAccessToken()).toBe(false);
});

test('401 responses from auth endpoints do not trigger refresh', async () => {
  const fetchMock = vi.fn(async () => response(401));
  vi.stubGlobal('fetch', fetchMock);

  await authFetch(new Request('http://x/api/v1/auth/login', { method: 'POST' }));

  expect(fetchMock).toHaveBeenCalledTimes(1);
});
