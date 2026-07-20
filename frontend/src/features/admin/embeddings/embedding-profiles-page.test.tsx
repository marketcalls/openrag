import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { EmbeddingProfilesPage } from './embedding-profiles-page';

const PROFILE_ID = '550e8400-e29b-41d4-a716-446655440001';
const DEPLOYMENT_ID = '550e8400-e29b-41d4-a716-446655440002';
const GENERATION_ID = '550e8400-e29b-41d4-a716-446655440003';

const profile = {
  id: PROFILE_ID,
  name: 'Enterprise multilingual',
  provider_kind: 'litellm',
  model_name: 'text-embedding-3-large',
  base_url: null,
  dimension: 3072,
  max_input_tokens: 8192,
  batch_size: 32,
  config_digest: '1234567890abcdef',
  enabled: true,
  key_fingerprint: '...cret sha256:abc123',
};

const readyDeployment = {
  id: DEPLOYMENT_ID,
  profile_id: PROFILE_ID,
  generation_id: GENERATION_ID,
  status: 'ready',
  total_versions: 8,
  completed_versions: 8,
  failed_versions: 0,
  scan_complete: true,
  created_at: '2026-07-20T10:00:00Z',
  updated_at: '2026-07-20T10:01:00Z',
  activated_at: null,
  failure_code: null,
};

function renderPage(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <EmbeddingProfilesPage />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('requests a governed reindex with the selected immutable profile', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.method === 'GET' && input.url.endsWith('/embedding-profiles')) {
      return Response.json([profile]);
    }
    if (input.method === 'GET' && input.url.endsWith('/embedding-deployments')) {
      return Response.json([]);
    }
    return Response.json({ ...readyDeployment, status: 'building' });
  });
  const user = userEvent.setup();
  renderPage(fetchMock);

  await user.click(await screen.findByRole('button', { name: 'Deploy and reindex' }));

  await waitFor(() =>
    expect(
      requests.some(
        (request) =>
          request.method === 'POST' && request.url.endsWith('/embedding-deployments'),
      ),
    ).toBe(true),
  );
  const post = requests.find(
    (request) =>
      request.method === 'POST' && request.url.endsWith('/embedding-deployments'),
  );
  if (!post) throw new Error('Expected embedding deployment POST');
  expect(await post.clone().json()).toEqual({ profile_id: PROFILE_ID });
});

test('activates only a deployment that completed verification', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.method === 'GET' && input.url.endsWith('/embedding-profiles')) {
      return Response.json([profile]);
    }
    if (input.method === 'GET' && input.url.endsWith('/embedding-deployments')) {
      return Response.json([readyDeployment]);
    }
    return Response.json({
      ...readyDeployment,
      status: 'active',
      activated_at: '2026-07-20T10:02:00Z',
    });
  });
  const user = userEvent.setup();
  renderPage(fetchMock);

  await user.click(await screen.findByRole('button', { name: 'Activate' }));

  await waitFor(() =>
    expect(
      requests.some(
        (request) =>
          request.method === 'POST' &&
          request.url.endsWith(`/embedding-deployments/${DEPLOYMENT_ID}/activate`),
      ),
    ).toBe(true),
  );
});
