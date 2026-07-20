import { setAccessToken } from '@/lib/auth-store';

import { acceptDurableRun, streamDurableRun } from './durable-stream';
import type { ChatSseEvent } from './stream';

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
        frame('e2', 'message.delta', { delta: 'Hello' }),
        frame('e3', 'message.completed', {
          message_id: 'm1',
          no_answer: false,
          citations: [],
        }),
        frame('e4', 'usage.updated', { prompt_tokens: 3, completion_tokens: 1 }),
        frame('e5', 'run.completed', {}),
      ]);
    }),
  );

  const controller = new AbortController();
  const accepted = await acceptDurableRun('c1', { content: 'hi' }, controller.signal);
  const events: ChatSseEvent[] = [];
  await streamDurableRun(accepted, (event) => events.push(event), controller.signal);

  expect(requests.map((request) => request.method)).toEqual(['POST', 'GET']);
  expect(await requests[0]!.clone().json()).toMatchObject({
    content: 'hi',
    client_request_id: expect.any(String),
  });
  expect(events.map((event) => event.type)).toEqual([
    'route_selected',
    'token',
    'citations',
    'done',
  ]);
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
