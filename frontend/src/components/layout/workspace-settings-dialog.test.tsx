import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { WorkspaceOut } from '@/api/types';

import { WorkspaceSettingsDialog } from './workspace-settings-dialog';

const workspace: WorkspaceOut = {
  id: 'workspace-1',
  name: 'Finance',
  embedding_model: 'bge-m3',
  min_score: 0.35,
  default_model_id: null,
};

function modelResponse() {
  return new Response(
    JSON.stringify([
      { id: 'model-1', display_name: 'Local model' },
      { id: 'model-2', display_name: 'Hosted model' },
    ]),
    { status: 200, headers: { 'content-type': 'application/json' } },
  );
}

function renderDialog(fetchMock: ReturnType<typeof vi.fn>, value = workspace) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <WorkspaceSettingsDialog workspace={value} open onOpenChange={vi.fn()} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('sets an enabled model as the workspace default', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.method === 'GET') return modelResponse();
    return new Response(
      JSON.stringify({ ...workspace, default_model_id: 'model-2' }),
      { status: 200, headers: { 'content-type': 'application/json' } },
    );
  });
  const user = userEvent.setup();
  renderDialog(fetchMock);

  await screen.findByRole('option', { name: 'Hosted model' });
  await user.selectOptions(screen.getByLabelText('Default model'), 'model-2');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));

  await waitFor(() =>
    expect(requests.some((request) => request.method === 'PATCH')).toBe(true),
  );
  const patchRequest = requests.find((request) => request.method === 'PATCH');
  if (!patchRequest) throw new Error('Expected workspace PATCH');
  expect(await patchRequest.clone().json()).toEqual({ default_model_id: 'model-2' });
});

test('clears the workspace default with automatic selection', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.method === 'GET') return modelResponse();
    return new Response(JSON.stringify(workspace), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    });
  });
  const user = userEvent.setup();
  renderDialog(fetchMock, { ...workspace, default_model_id: 'model-1' });

  const automatic = await screen.findByRole('option', { name: /Automatic/ });
  expect(automatic).toHaveValue('');
  fireEvent.change(screen.getByLabelText('Default model'), { target: { value: '' } });
  expect(screen.getByLabelText('Default model')).toHaveValue('');
  await user.click(screen.getByRole('button', { name: 'Save changes' }));

  await waitFor(() =>
    expect(requests.some((request) => request.method === 'PATCH')).toBe(true),
  );
  const patchRequest = requests.find((request) => request.method === 'PATCH');
  if (!patchRequest) throw new Error('Expected workspace PATCH');
  expect(await patchRequest.clone().json()).toEqual({ default_model_id: null });
});
