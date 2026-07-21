import { setAccessToken } from '@/lib/auth-store';

import {
  acceptDurableRegeneration,
  acceptDurableRun,
  streamDurableRun,
} from './durable-stream';
import type { ChatSseEvent } from './stream';

const ARTIFACT = {
  schema_version: 'analytics.v1',
  title: 'Revenue dashboard',
  subtitle: null,
  kpis: [],
  blocks: [
    {
      kind: 'explainer',
      title: 'Summary',
      body_markdown: 'Revenue increased [1].',
      source_markers: [1],
    },
  ],
  suggested_followups: [],
};

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const frame of frames) controller.enqueue(encoder.encode(frame));
        controller.close();
      },
    }),
    { status: 200, headers: { 'content-type': 'text/event-stream' } },
  );
}

function frame(id: string, event: string, payload: Record<string, unknown>): string {
  return `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify({
    event_type: event,
    run_id: 'r1',
    payload,
  })}\n\n`;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('accepts a run and replays its typed durable stream to completion', async () => {
  setAccessToken('token');
  const requests: Request[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      requests.push(request);
      if (request.method === 'POST') {
        return new Response(JSON.stringify({ run_id: 'r1', events_url: '/events/r1' }), {
          status: 202,
          headers: { 'content-type': 'application/json' },
        });
      }
      return sseResponse([
        frame('e1', 'route.selected', { route: 'direct', reason_code: 'greeting' }),
        frame('e2', 'agent.started', { reason_code: 'weak_evidence' }),
        frame('e3', 'tool.started', { iteration: 1, tool: 'search' }),
        frame('e4', 'tool.completed', { iteration: 1, tool: 'search' }),
        frame('e5', 'agent.completed', { finish_reason: 'planner_finished' }),
        frame('e6', 'message.delta', { delta: 'Hello' }),
        frame('e7', 'artifact.created', { message_id: 'm1', artifact: ARTIFACT }),
        frame('e8', 'message.completed', {
          message_id: 'm1',
          no_answer: false,
          citations: [],
        }),
        frame('e9', 'usage.updated', { prompt_tokens: 3, completion_tokens: 1 }),
        frame('e10', 'run.completed', {}),
      ]);
    }),
  );

  const controller = new AbortController();
  const accepted = await acceptDurableRun(
    'c1',
    { content: 'hi', model_id: 'm1', reasoning_effort: 'high' },
    controller.signal,
  );
  const events: ChatSseEvent[] = [];
  await streamDurableRun(accepted, (event) => events.push(event), controller.signal);

  expect(requests.map((request) => request.method)).toEqual(['POST', 'GET']);
  expect(await requests[0]!.clone().json()).toMatchObject({
    content: 'hi',
    model_id: 'm1',
    reasoning_effort: 'high',
    client_request_id: expect.any(String),
  });
  expect(events.map((event) => event.type)).toEqual([
    'route_selected',
    'agent_started',
    'tool_progress',
    'tool_progress',
    'agent_completed',
    'token',
    'artifact',
    'citations',
    'done',
  ]);
  expect(events.find((event) => event.type === 'artifact')).toEqual({
    type: 'artifact',
    artifact: ARTIFACT,
  });
  expect(events.at(-1)).toEqual({
    type: 'done',
    done: {
      message_id: 'm1',
      prompt_tokens: 3,
      completion_tokens: 1,
      no_answer: false,
    },
  });
});

test('drops an invalid durable artifact but preserves the grounded answer', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        frame('e1', 'message.delta', { delta: 'Grounded answer [1].' }),
        frame('e2', 'artifact.created', {
          message_id: 'm1',
          artifact: { ...ARTIFACT, component: '<script>alert(1)</script>' },
        }),
        frame('e3', 'message.completed', {
          message_id: 'm1',
          no_answer: false,
          citations: [],
        }),
        frame('e4', 'run.completed', {}),
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];

  await streamDurableRun(
    { run_id: 'r1', events_url: '/events/r1' },
    (event) => events.push(event),
    new AbortController().signal,
  );

  expect(events.some((event) => event.type === 'artifact')).toBe(false);
  expect(events.map((event) => event.type)).toEqual(['token', 'citations', 'done']);
});

test('recovers a failed terminal status when the event stream is unavailable', async () => {
  const requests: Request[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (request: Request) => {
      requests.push(request);
      if (request.url.endsWith('/events/r1')) return sseResponse([]);
      return new Response(
        JSON.stringify({
          run_id: 'r1',
          status: 'failed',
          error_code: 'retrieval_failed',
        }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      );
    }),
  );
  const events: ChatSseEvent[] = [];

  await streamDurableRun(
    { run_id: 'r1', events_url: '/events/r1' },
    (event) => events.push(event),
    new AbortController().signal,
  );

  expect(requests.map((request) => new URL(request.url).pathname)).toEqual([
    '/events/r1',
    '/api/v1/runs/r1',
  ]);
  expect(events).toEqual([{ type: 'error', detail: 'retrieval_failed' }]);
});

test('regeneration preserves the selected model and reasoning effort', async () => {
  const requests: Request[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: Request) => {
      requests.push(input);
      return new Response(JSON.stringify({ run_id: 'r2', events_url: '/events/r2' }), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      });
    }),
  );

  await acceptDurableRegeneration(
    'assistant-1',
    'model-1',
    'medium',
    new AbortController().signal,
  );

  const request = requests[0];
  if (!request) throw new Error('Expected a Request');
  expect(await request.clone().json()).toMatchObject({
    model_id: 'model-1',
    reasoning_effort: 'medium',
    client_request_id: expect.any(String),
  });
});
