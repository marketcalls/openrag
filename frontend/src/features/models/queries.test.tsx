import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';

import { modelRefetchInterval, useModels } from './queries';

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

test('keeps polling while no probed chat model is ready', () => {
  expect(modelRefetchInterval(undefined)).toBe(1_000);
  expect(modelRefetchInterval([])).toBe(1_000);
  expect(
    modelRefetchInterval([
      {
        id: 'model-1',
        display_name: 'GPT-4o mini',
        supports_reasoning: false,
        default_reasoning_effort: 'off',
      },
    ]),
  ).toBe(false);
});

test('recovers when a background probe makes a model available', async () => {
  let requestCount = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => {
      requestCount += 1;
      return Response.json(
        requestCount === 1
          ? []
          : [
              {
                id: 'model-1',
                display_name: 'GPT-4o mini',
                supports_reasoning: false,
                default_reasoning_effort: 'off',
              },
            ],
      );
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const { result } = renderHook(() => useModels(), { wrapper });

  await waitFor(() => expect(result.current.data).toEqual([]));
  await waitFor(
    () => expect(result.current.data?.[0]?.display_name).toBe('GPT-4o mini'),
    { timeout: 2_500 },
  );

  expect(requestCount).toBe(2);
  client.clear();
});
