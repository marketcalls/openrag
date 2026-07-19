import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { EmbeddingProfileOut } from '@/api/types';

import { EmbeddingProfileDialog } from './embedding-profile-dialog';

const existing: EmbeddingProfileOut = {
  id: '260f51ce-8c05-4d87-9579-96da4f27497e',
  name: 'Production BGE',
  provider_kind: 'litellm',
  model_name: 'huggingface/BAAI/bge-m3',
  dimension: 1024,
  max_input_tokens: 8192,
  batch_size: 32,
  config_digest: 'a'.repeat(64),
  enabled: true,
};

function renderDialog(fetchMock = vi.fn(), profile?: EmbeddingProfileOut) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <EmbeddingProfileDialog open onOpenChange={vi.fn()} profile={profile} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('offers only gateway, local TEI, and development embedding paths', () => {
  renderDialog();

  expect(screen.getByRole('option', { name: 'LiteLLM gateway' })).toBeInTheDocument();
  expect(screen.getByRole('option', { name: 'Local TEI service' })).toBeInTheDocument();
  expect(
    screen.getByRole('option', { name: 'Deterministic hash — development only' }),
  ).toBeInTheDocument();
  expect(screen.queryByRole('option', { name: 'OpenAI' })).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/api key/i)).not.toBeInTheDocument();
});

test('submits the immutable vector contract', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    new Response(JSON.stringify(existing), {
      status: 201,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock);

  await user.type(screen.getByLabelText('Profile name'), 'Production BGE');
  await user.type(screen.getByLabelText('Model identifier'), 'huggingface/BAAI/bge-m3');
  await user.click(screen.getByRole('button', { name: 'Register profile' }));

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const request = fetchMock.mock.calls[0]?.[0];
  if (!(request instanceof Request)) throw new Error('Expected API client Request');
  expect(await request.clone().json()).toEqual({
    name: 'Production BGE',
    provider_kind: 'litellm',
    model_name: 'huggingface/BAAI/bge-m3',
    dimension: 1024,
    max_input_tokens: 8192,
    batch_size: 32,
  });
});

test('editing cannot mutate vector identity and sends only the new name', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    new Response(JSON.stringify({ ...existing, name: 'Primary BGE' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock, existing);

  expect(screen.getByLabelText('Provider path')).toBeDisabled();
  expect(screen.getByLabelText('Model identifier')).toBeDisabled();
  expect(screen.getByLabelText('Dimensions')).toBeDisabled();
  await user.clear(screen.getByLabelText('Profile name'));
  await user.type(screen.getByLabelText('Profile name'), 'Primary BGE');
  await user.click(screen.getByRole('button', { name: 'Save name' }));

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const request = fetchMock.mock.calls[0]?.[0];
  if (!(request instanceof Request)) throw new Error('Expected API client Request');
  expect(request.method).toBe('PATCH');
  expect(await request.clone().json()).toEqual({ name: 'Primary BGE' });
});
