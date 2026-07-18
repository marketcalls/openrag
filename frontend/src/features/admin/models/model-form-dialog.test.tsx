import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ModelOut } from '@/api/types';

import { ModelFormDialog } from './model-form-dialog';

const existingModel: ModelOut = {
  id: 'model-existing',
  display_name: 'Private gateway',
  litellm_model_name: 'acme/private-v1',
  provider_kind: 'openai_compatible',
  base_url: 'https://models.acme.test/v1',
  enabled: true,
  key_fingerprint: '...7890 sha256:abc123',
  sync_status: 'synced',
};

function renderDialog(fetchMock = vi.fn(), model?: ModelOut) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ModelFormDialog open onOpenChange={vi.fn()} model={model} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('base URL appears only for ollama and openai compatible providers', async () => {
  const user = userEvent.setup();
  renderDialog();

  expect(screen.queryByLabelText('Base URL')).not.toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Provider'), 'ollama');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
  await user.selectOptions(screen.getByLabelText('Provider'), 'openai_compatible');
  expect(screen.getByLabelText('Base URL')).toBeInTheDocument();
});

test('api key is write-only and absent for ollama', async () => {
  const user = userEvent.setup();
  renderDialog();

  const key = screen.getByLabelText('API key');
  expect(key).toHaveAttribute('type', 'password');
  expect(key).toHaveAttribute('autocomplete', 'off');
  await user.selectOptions(screen.getByLabelText('Provider'), 'ollama');
  expect(screen.queryByLabelText('API key')).not.toBeInTheDocument();
});

test('submits the assembled model payload', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    new Response(
      JSON.stringify({
        id: 'm1',
        display_name: 'GPT-4o mini',
        litellm_model_name: 'gpt-4o-mini',
        provider_kind: 'openai',
        base_url: null,
        enabled: true,
        key_fingerprint: 'ab12…ef90',
        sync_status: 'pending',
      }),
      { status: 201, headers: { 'content-type': 'application/json' } },
    ),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock);

  await user.type(screen.getByLabelText('Display name'), 'GPT-4o mini');
  await user.type(screen.getByLabelText('Model id'), 'gpt-4o-mini');
  await user.type(screen.getByLabelText('API key'), 'sk-test-123');
  await user.click(screen.getByRole('button', { name: 'Add model' }));

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const request = fetchMock.mock.calls[0]?.[0];
  expect(request).toBeInstanceOf(Request);
  if (!(request instanceof Request)) throw new Error('Expected the API client to send a Request');
  const body = JSON.parse(await request.clone().text()) as Record<string, unknown>;
  expect(body).toMatchObject({
    display_name: 'GPT-4o mini',
    litellm_model_name: 'gpt-4o-mini',
    provider_kind: 'openai',
    api_key: 'sk-test-123',
  });
});

test('edits model metadata without resending the stored api key', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    new Response(JSON.stringify({ ...existingModel, display_name: 'Private gateway v2' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock, existingModel);

  expect(screen.getByLabelText('Display name')).toHaveValue('Private gateway');
  expect(screen.getByLabelText('Base URL')).toHaveValue('https://models.acme.test/v1');
  expect(screen.getByLabelText('API key')).toHaveValue('');
  expect(screen.queryByDisplayValue(/abc123|7890/)).not.toBeInTheDocument();
  await user.clear(screen.getByLabelText('Display name'));
  await user.type(screen.getByLabelText('Display name'), 'Private gateway v2');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const request = fetchMock.mock.calls[0]?.[0];
  if (!(request instanceof Request)) throw new Error('Expected API client Request');
  expect(request.method).toBe('PATCH');
  const body = (await request.clone().json()) as Record<string, unknown>;
  expect(body).toEqual({
    display_name: 'Private gateway v2',
    base_url: 'https://models.acme.test/v1',
  });
  expect(body).not.toHaveProperty('api_key');
});

test('includes a replacement api key only when explicitly entered', async () => {
  const fetchMock = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) =>
    new Response(JSON.stringify(existingModel), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }),
  );
  const user = userEvent.setup();
  renderDialog(fetchMock, existingModel);

  await user.type(screen.getByLabelText('API key'), 'sk-replacement');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));

  await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());
  const request = fetchMock.mock.calls[0]?.[0];
  if (!(request instanceof Request)) throw new Error('Expected API client Request');
  expect(await request.clone().json()).toMatchObject({ api_key: 'sk-replacement' });
});
