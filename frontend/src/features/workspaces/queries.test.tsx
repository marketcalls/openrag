import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { useAddWorkspaceMember, usePatchWorkspace } from './queries';

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      {children}
    </QueryClientProvider>
  );
}

afterEach(() => vi.unstubAllGlobals());

function firstRequest(fetchMock: ReturnType<typeof vi.fn>): Request {
  const request = fetchMock.mock.calls[0]?.[0] as unknown;
  if (!(request instanceof Request)) throw new Error('Expected API client Request');
  return request;
}

test('adds a user to a workspace as a member', async () => {
  const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
  vi.stubGlobal('fetch', fetchMock);
  const { result } = renderHook(() => useAddWorkspaceMember(), { wrapper });

  act(() => result.current.mutate({ workspaceId: 'workspace-1', userId: 'user-1' }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());

  const request = firstRequest(fetchMock);
  expect(request.method).toBe('POST');
  expect(request.url).toContain('/api/v1/workspaces/workspace-1/members');
  expect(await request.clone().json()).toEqual({ user_id: 'user-1' });
});

test('sets the workspace default model', async () => {
  const fetchMock = vi.fn(async () =>
    new Response(
      JSON.stringify({
        id: 'workspace-1',
        name: 'Finance',
        embedding_model: 'bge-m3',
        min_score: 0.35,
        default_model_id: 'model-2',
      }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    ),
  );
  vi.stubGlobal('fetch', fetchMock);
  const { result } = renderHook(() => usePatchWorkspace(), { wrapper });

  act(() =>
    result.current.mutate({ workspaceId: 'workspace-1', defaultModelId: 'model-2' }),
  );
  await waitFor(() => expect(fetchMock).toHaveBeenCalled());

  const request = firstRequest(fetchMock);
  expect(request.method).toBe('PATCH');
  expect(await request.clone().json()).toEqual({ default_model_id: 'model-2' });
});
