import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { WorkspaceProvider } from '@/features/workspaces/workspace-context';

import { MemoryPage } from './memory-page';

const WORKSPACE_ID = '550e8400-e29b-41d4-a716-446655440001';
const MEMORY_ID = '550e8400-e29b-41d4-a716-446655440002';

function renderPage(fetchMock: ReturnType<typeof vi.fn>) {
  localStorage.setItem('openrag-workspace:anonymous', WORKSPACE_ID);
  vi.stubGlobal('fetch', fetchMock);
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <WorkspaceProvider>
        <MemoryPage />
      </WorkspaceProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

test('shows provenance-bound memories and enables explicit creation', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.url.endsWith('/api/v1/workspaces')) {
      return Response.json([
        {
          id: WORKSPACE_ID,
          name: 'Finance',
          embedding_model: 'bge-m3',
          min_score: 0.35,
          default_model_id: null,
        },
      ]);
    }
    if (input.url.endsWith('/preferences')) {
      return Response.json({
        workspace_id: WORKSPACE_ID,
        extraction_enabled: false,
        semantic_enabled: true,
        episodic_enabled: false,
        procedural_enabled: false,
        updated_at: '2026-07-20T10:00:00Z',
      });
    }
    if (input.method === 'POST') {
      return Response.json(
        {
          id: '550e8400-e29b-41d4-a716-446655440099',
          workspace_id: WORKSPACE_ID,
          canonical_key: 'response.style',
          content: 'Prefer concise answers.',
          structured_value: null,
          memory_type: 'semantic',
          scope: 'user_workspace',
          status: 'active',
          confidence: 1,
          importance: 0.5,
          sensitivity: 'internal',
          expires_at: null,
          created_at: '2026-07-20T10:00:00Z',
          updated_at: '2026-07-20T10:00:00Z',
          provenance: [],
        },
        { status: 201 },
      );
    }
    return Response.json({
      items: [
        {
          id: MEMORY_ID,
          workspace_id: WORKSPACE_ID,
          canonical_key: 'answer.format',
          content: 'Use tables for comparisons.',
          structured_value: null,
          memory_type: 'semantic',
          scope: 'user_workspace',
          status: 'active',
          confidence: 1,
          importance: 0.8,
          sensitivity: 'internal',
          expires_at: null,
          created_at: '2026-07-20T10:00:00Z',
          updated_at: '2026-07-20T10:00:00Z',
          provenance: [
            {
              source_kind: 'explicit_user_action',
              source_event_id: '550e8400-e29b-41d4-a716-446655440003',
              source_message_id: null,
              source_hash: 'a'.repeat(64),
              created_at: '2026-07-20T10:00:00Z',
            },
          ],
        },
      ],
      next_cursor: null,
    });
  });
  const user = userEvent.setup();
  renderPage(fetchMock);

  expect(await screen.findByText('Use tables for comparisons.')).toBeInTheDocument();
  expect(screen.getByText('Explicit user action')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Add memory' }));
  await user.type(screen.getByLabelText('Memory key'), 'response.style');
  await user.type(
    screen.getByLabelText('What should OpenRAG remember?'),
    'Prefer concise answers.',
  );
  await user.click(screen.getByRole('button', { name: 'Save memory' }));

  await waitFor(() =>
    expect(
      requests.some((request) => request.method === 'POST' && request.url.endsWith('/memories')),
    ).toBe(true),
  );
  const post = requests.find(
    (request) => request.method === 'POST' && request.url.endsWith('/memories'),
  );
  if (!post) throw new Error('Expected memory POST');
  expect(await post.clone().json()).toMatchObject({
    canonical_key: 'response.style',
    content: 'Prefer concise answers.',
    memory_type: 'semantic',
    scope: 'user_workspace',
  });
});

test('makes automatic extraction an explicit opt-in', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    if (input.url.endsWith('/api/v1/workspaces')) {
      return Response.json([
        {
          id: WORKSPACE_ID,
          name: 'Finance',
          embedding_model: 'bge-m3',
          min_score: 0.35,
          default_model_id: null,
        },
      ]);
    }
    if (input.url.endsWith('/preferences')) {
      return Response.json({
        workspace_id: WORKSPACE_ID,
        extraction_enabled: input.method === 'PATCH',
        semantic_enabled: true,
        episodic_enabled: false,
        procedural_enabled: false,
        updated_at: '2026-07-20T10:00:00Z',
      });
    }
    return Response.json({ items: [], next_cursor: null });
  });
  const user = userEvent.setup();
  renderPage(fetchMock);

  const toggle = await screen.findByRole('checkbox', { name: 'Automatic memory extraction' });
  expect(toggle).not.toBeChecked();
  await user.click(toggle);

  await waitFor(() =>
    expect(
      requests.some(
        (request) => request.method === 'PATCH' && request.url.endsWith('/preferences'),
      ),
    ).toBe(true),
  );
});
