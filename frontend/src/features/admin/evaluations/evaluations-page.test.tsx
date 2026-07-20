import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { WorkspaceProvider } from '@/features/workspaces/workspace-context';

import { EvaluationsPage } from './evaluations-page';

const WORKSPACE_ID = '550e8400-e29b-41d4-a716-446655440001';
const DATASET_ID = '550e8400-e29b-41d4-a716-446655440002';
const VERSION_ID = '550e8400-e29b-41d4-a716-446655440003';
const MODEL_ID = '550e8400-e29b-41d4-a716-446655440004';
const JUDGE_MODEL_ID = '550e8400-e29b-41d4-a716-446655440005';

const version = {
  id: VERSION_ID,
  org_id: '550e8400-e29b-41d4-a716-446655440010',
  workspace_id: WORKSPACE_ID,
  dataset_id: DATASET_ID,
  version: 2,
  label: 'Approved policy baseline',
  status: 'sealed',
  case_count: 12,
  content_digest: 'abc123',
  created_by: '550e8400-e29b-41d4-a716-446655440011',
  created_at: '2026-07-20T09:00:00Z',
  sealed_at: '2026-07-20T09:00:00Z',
};

const run = (id: string, recall: number, createdAt: string) => ({
  id,
  org_id: version.org_id,
  workspace_id: WORKSPACE_ID,
  dataset_version_id: VERSION_ID,
  model_id: MODEL_ID,
  evaluator_model_id: null,
  use_llm_judge: false,
  policy_id: null,
  trigger_kind: 'manual',
  trigger_key: null,
  status: 'completed',
  max_cases: 12,
  max_tokens: 24000,
  max_cost_microusd: 500000,
  total_cases: 12,
  completed_cases: 12,
  failed_cases: 0,
  consumed_tokens: 8200,
  consumed_cost_microusd: 125000,
  error_code: null,
  recall,
  precision: 0.84,
  mrr: 0.8,
  ndcg: 0.81,
  citation_precision: 0.94,
  citation_recall: 0.88,
  groundedness: 0.91,
  answer_relevance: 0.86,
  correct_refusal: 1,
  created_by: version.created_by,
  created_at: createdAt,
  started_at: createdAt,
  finished_at: createdAt,
});

function responseFor(request: Request) {
  const url = new URL(request.url);
  if (url.pathname.endsWith('/workspaces')) {
    return [{ id: WORKSPACE_ID, name: 'Engineering', embedding_model: 'bge-m3', min_score: 0.35, default_model_id: MODEL_ID }];
  }
  if (url.pathname.endsWith('/admin/models')) {
    return [
      { id: MODEL_ID, display_name: 'Production model', enabled: true, supports_chat_completion: true, supports_structured_json: false, supports_verifier: false },
      { id: JUDGE_MODEL_ID, display_name: 'Verifier model', enabled: true, supports_chat_completion: true, supports_structured_json: true, supports_verifier: true },
    ];
  }
  if (url.pathname.endsWith('/evaluations/datasets')) {
    return [{ id: DATASET_ID, org_id: version.org_id, workspace_id: WORKSPACE_ID, name: 'Policy grounding', description: 'Golden policy questions', archived: false, created_by: version.created_by, created_at: version.created_at, updated_at: version.created_at }];
  }
  if (url.pathname.endsWith(`/datasets/${DATASET_ID}/versions`)) return [version];
  if (url.pathname.endsWith('/evaluations/policies') && request.method === 'PUT') {
    return {
      id: '550e8400-e29b-41d4-a716-446655440030',
      org_id: version.org_id,
      workspace_id: WORKSPACE_ID,
      dataset_id: DATASET_ID,
      model_id: MODEL_ID,
      evaluator_model_id: null,
      use_llm_judge: false,
      enabled: true,
      trigger_on_config_change: true,
      interval_hours: 24,
      max_cases: 12,
      max_tokens: 50000,
      max_cost_microusd: 5000000,
      next_run_at: '2026-07-21T11:00:00Z',
      last_enqueued_at: null,
      last_error_code: null,
      created_by: version.created_by,
      created_at: version.created_at,
      updated_at: version.created_at,
    };
  }
  if (url.pathname.endsWith('/evaluations/policies')) return [];
  if (url.pathname.endsWith('/evaluations/runs') && request.method === 'POST') {
    return { ...run('550e8400-e29b-41d4-a716-446655440099', 0, '2026-07-20T11:00:00Z'), status: 'queued', completed_cases: 0 };
  }
  if (url.pathname.endsWith('/evaluations/runs')) {
    return [
      run('550e8400-e29b-41d4-a716-446655440020', 0.92, '2026-07-20T10:00:00Z'),
      run('550e8400-e29b-41d4-a716-446655440021', 0.81, '2026-07-19T10:00:00Z'),
    ];
  }
  throw new Error(`Unhandled request: ${request.method} ${request.url}`);
}

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

test('requires budget confirmation, configures automation, and compares regressions accessibly', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    return Response.json(responseFor(input), {
      status: input.method === 'POST' ? 202 : 200,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  const user = userEvent.setup();

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <WorkspaceProvider><EvaluationsPage /></WorkspaceProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );

  expect(await screen.findByRole('heading', { name: 'RAG evaluations' })).toBeVisible();
  expect(await screen.findByRole('table', { name: 'Evaluation metric comparison' })).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Run evaluation' }));
  expect(screen.getByLabelText('Use LLM judge')).not.toBeChecked();
  await user.click(screen.getByLabelText('Use LLM judge'));
  expect(screen.getByLabelText('Evaluator model')).toHaveValue(JUDGE_MODEL_ID);
  expect(screen.getByLabelText('Maximum evaluation tokens')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Queue evaluation' })).toBeDisabled();

  await user.click(screen.getByLabelText('I confirm this evaluation budget'));
  await user.click(screen.getByRole('button', { name: 'Queue evaluation' }));
  expect(await screen.findByText('Evaluation queued')).toBeVisible();

  await user.click(screen.getByRole('button', { name: 'Automation' }));
  expect(screen.getByLabelText('Automation model')).toHaveValue(MODEL_ID);
  expect(screen.getByLabelText('Run every hours')).toHaveValue(24);
  expect(screen.getByRole('button', { name: 'Save automation' })).toBeDisabled();
  await user.click(screen.getByLabelText('I confirm this recurring evaluation budget'));
  await user.click(screen.getByRole('button', { name: 'Save automation' }));
  expect(await screen.findByText('Evaluation automation saved')).toBeVisible();

  const policyRequest = fetchMock.mock.calls
    .map(([input]) => input)
    .find((input) => input instanceof Request && input.method === 'PUT');
  expect(policyRequest).toBeInstanceOf(Request);
  await expect((policyRequest as Request).clone().json()).resolves.toMatchObject({
    dataset_id: DATASET_ID,
    model_id: MODEL_ID,
    interval_hours: 24,
    max_cases: 12,
    max_tokens: 50000,
    max_cost_microusd: 5000000,
    trigger_on_config_change: true,
  });
});
