import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ModelOut } from '@/api/types';

import { ModelsPage } from './models-page';

const model: ModelOut = {
  id: '550e8400-e29b-41d4-a716-446655440040',
  display_name: 'GPT-5 mini',
  litellm_model_name: 'gpt-5-mini',
  provider_kind: 'openai',
  base_url: null,
  enabled: true,
  is_utility: false,
  key_fingerprint: '...-key sha256:abc',
  supports_chat_completion: true,
  supports_streaming: true,
  supports_structured_json: true,
  supports_verifier: true,
  supports_tools: true,
  supports_vision: false,
  context_window: 128000,
  supports_reasoning: true,
  default_reasoning_effort: 'medium',
  probe_status: 'passed',
  probe_revision: 1,
  probe_latency_ms: 320,
  last_probe_error_code: null,
  last_probed_at: '2026-07-20T10:00:00Z',
};

afterEach(() => vi.unstubAllGlobals());

test('shows measured capabilities and queues an on-demand connection test', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.method === 'POST' && input.url.endsWith(`/models/${model.id}/probe`)) {
      return Response.json({
        id: '550e8400-e29b-41d4-a716-446655440041',
        model_id: model.id,
        revision: 2,
        status: 'queued',
        supports_chat_completion: false,
        supports_streaming: false,
        supports_structured_json: false,
        supports_tools: false,
        supports_vision: false,
        supports_reasoning: false,
        context_window: null,
        latency_ms: null,
        error_code: null,
        requested_by: '550e8400-e29b-41d4-a716-446655440042',
        created_at: '2026-07-20T11:00:00Z',
        started_at: null,
        completed_at: null,
      }, { status: 202 });
    }
    return Response.json([model]);
  });
  vi.stubGlobal('fetch', fetchMock);
  const user = userEvent.setup();

  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelsPage />
    </QueryClientProvider>,
  );

  expect(await screen.findByText('Probe passed')).toBeVisible();
  expect(screen.getByText('Chat · Stream · JSON · Tools · Judge')).toBeVisible();
  expect(screen.getByText('128k')).toBeVisible();
  await user.click(screen.getByRole('radio', { name: 'Use GPT-5 mini for background AI tasks' }));
  await vi.waitFor(() => {
    const request = requests.find((item) => item.method === 'PATCH');
    expect(request).toBeDefined();
  });
  const utilityPatch = requests.find((item) => item.method === 'PATCH');
  expect(await utilityPatch?.clone().json()).toEqual({ is_utility: true });
  await user.click(screen.getByRole('button', { name: 'Test GPT-5 mini connection' }));

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
    expect.objectContaining({ method: 'POST' }),
  ));
});
