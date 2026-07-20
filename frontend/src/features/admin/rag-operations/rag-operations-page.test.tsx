import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import { RagOperationsPage } from './rag-operations-page';

const RUN_ID = '550e8400-e29b-41d4-a716-446655440010';
const ISSUE_ID = '550e8400-e29b-41d4-a716-446655440020';

const overview = {
  query_count: 1280,
  grounded_count: 1090,
  no_answer_count: 120,
  failed_count: 55,
  cancelled_count: 15,
  grounded_rate: 0.8516,
  no_answer_rate: 0.0938,
  p50_latency_ms: 820,
  p95_latency_ms: 2410,
  p99_latency_ms: 3900,
  average_ttft_ms: 310,
  prompt_tokens: 924000,
  completion_tokens: 188000,
  estimated_cost_microusd: 4200000,
};

const run = {
  id: '550e8400-e29b-41d4-a716-446655440011',
  org_id: '550e8400-e29b-41d4-a716-446655440001',
  workspace_id: '550e8400-e29b-41d4-a716-446655440002',
  run_id: RUN_ID,
  model_id: null,
  trace_id: '0123456789abcdef0123456789abcdef',
  environment: 'production',
  release: '2026.07.20',
  route: 'rag',
  outcome: 'grounded',
  error_code: null,
  latency_ms: 1250,
  ttft_ms: 280,
  route_ms: 18,
  retrieval_ms: 210,
  provider_ms: 880,
  persistence_ms: 42,
  prompt_tokens: 1480,
  completion_tokens: 260,
  retrieval_count: 8,
  citation_count: 4,
  memory_item_count: 2,
  attempts: 1,
  estimated_cost_microusd: 4200,
  accepted_at: '2026-07-20T10:00:00Z',
  finished_at: '2026-07-20T10:00:01.250Z',
};

const issue = {
  id: ISSUE_ID,
  fingerprint: 'a'.repeat(64),
  category: 'retrieval',
  code: 'retrieval.timeout',
  service: 'api',
  environment: 'production',
  exception_type: 'TimeoutError',
  top_frame: 'openrag.modules.retrieval.service:retrieve',
  status: 'open',
  alert_state: 'firing',
  owner: null,
  first_release: '2026.07.19',
  last_release: '2026.07.20',
  occurrence_count: 14,
  first_seen_at: '2026-07-19T10:00:00Z',
  last_seen_at: '2026-07-20T10:02:00Z',
  resolved_at: null,
};

function responseFor(request: Request) {
  const url = new URL(request.url);
  if (url.pathname.endsWith(`/runs/${RUN_ID}`)) return run;
  if (url.pathname.endsWith(`/errors/${ISSUE_ID}`)) {
    return {
      issue,
      occurrences: [{
        id: '550e8400-e29b-41d4-a716-446655440021',
        issue_id: ISSUE_ID,
        org_id: run.org_id,
        workspace_id: run.workspace_id,
        run_id: RUN_ID,
        trace_id: run.trace_id,
        code: issue.code,
        exception_type: issue.exception_type,
        http_method: 'POST',
        route_template: '/api/v1/chats/{chat_id}/runs',
        http_status: 504,
        release: issue.last_release,
        occurred_at: issue.last_seen_at,
      }],
    };
  }
  if (url.pathname.endsWith('/overview')) return overview;
  if (url.pathname.endsWith('/series')) {
    return [
      { bucket: '2026-07-20T09:00:00Z', query_count: 540, grounded_count: 460, no_answer_count: 51, failed_count: 22, p95_latency_ms: 2300 },
      { bucket: '2026-07-20T10:00:00Z', query_count: 740, grounded_count: 630, no_answer_count: 69, failed_count: 33, p95_latency_ms: 2410 },
    ];
  }
  if (url.pathname.endsWith('/runs')) return { items: [run], next_cursor: null };
  if (url.pathname.endsWith('/errors')) return { items: [issue], next_cursor: null };
  throw new Error(`Unhandled request: ${request.url}`);
}

function renderPage(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal('fetch', fetchMock);
  render(
    <MemoryRouter initialEntries={['/admin/rag-operations?range=24h']}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <RagOperationsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('coordinates filters across the operations overview, chart, runs, and errors', async () => {
  const requests: Request[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    requests.push(input);
    return Response.json(responseFor(input));
  });
  const user = userEvent.setup();
  renderPage(fetchMock);

  expect(await screen.findByRole('heading', { name: 'RAG operations' })).toBeVisible();
  expect(await screen.findByText('1,280')).toBeVisible();
  expect(screen.getByText('2.41s')).toBeVisible();
  expect(screen.getByRole('img', { name: 'Query volume and p95 latency over time' })).toBeVisible();
  expect(screen.getByRole('table', { name: 'Query throughput data' })).toBeInTheDocument();
  expect(screen.getByRole('heading', { name: 'Recent runs' })).toBeVisible();
  expect(screen.getByRole('heading', { name: 'Active error groups' })).toBeVisible();

  await user.selectOptions(screen.getByLabelText('Route'), 'rag');
  await waitFor(() => {
    const overviewRequests = requests.filter((request) => request.url.includes('/overview?'));
    expect(overviewRequests.at(-1)?.url).toContain('route=rag');
  });
});

test('opens safe, content-free run and error drilldowns', async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    if (!(input instanceof Request)) throw new Error('Expected Request');
    return Response.json(responseFor(input));
  });
  const user = userEvent.setup();
  renderPage(fetchMock);

  await user.click(await screen.findByRole('button', { name: `Inspect run ${RUN_ID}` }));
  expect(await screen.findByRole('dialog')).toHaveTextContent('Run trace');
  expect(screen.getByRole('dialog')).toHaveTextContent(run.trace_id);
  await user.click(screen.getByRole('button', { name: 'Close' }));

  await user.click(screen.getByRole('button', { name: `Inspect error ${issue.code}` }));
  expect(await screen.findByRole('dialog')).toHaveTextContent(issue.exception_type);
  expect(screen.getByRole('dialog')).not.toHaveTextContent('prompt');
});
