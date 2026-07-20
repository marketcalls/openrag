import { setAccessToken } from '@/lib/auth-store';

import { streamChatSse, type ChatSseEvent } from './stream';

function sseResponse(frames: string[]): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { 'content-type': 'text/event-stream' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

test('emits authoritative typed events in order across transport chunks', async () => {
  setAccessToken('token');
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: route_selected\ndata: {"route":"rag","reason_code":"substantive_default"}\n\n',
        'event: retrieval_started\ndata: {}\n\n',
        'event: sources\ndata: {"sources":[{"marker":1,"document_id":"d1","filename":"a.pdf","page":3,"chunk_index":2,"score":0.7,"snippet":"evidence"}]}\n\n',
        'event: token\ndata: {"del',
        'ta":"Hel"}\n\nevent: token\ndata: {"delta":"lo"}\n\n',
        'event: citations\ndata: {"citations":[{"marker":1,"document_id":"d1","chunk_ref":"d1:3:2","page":3,"score":0.7}]}\n\n',
        'event: done\ndata: {"message_id":"m1","prompt_tokens":10,"completion_tokens":5,"no_answer":false}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];

  await streamChatSse(
    '/api/v1/chats/c1/messages',
    { content: 'hi' },
    (event) => events.push(event),
    new AbortController().signal,
  );

  expect(events.map((event) => event.type)).toEqual([
    'route_selected',
    'retrieval_started',
    'sources',
    'token',
    'token',
    'citations',
    'done',
  ]);
  const tokens = events.filter(
    (event): event is Extract<ChatSseEvent, { type: 'token' }> => event.type === 'token',
  );
  expect(tokens.map((event) => event.delta).join('')).toBe('Hello');
  expect(events[0]).toEqual({
    type: 'route_selected',
    route: 'rag',
    reasonCode: 'substantive_default',
  });
});

test('rejects an unknown public route code', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: route_selected\ndata: {"route":"secret_tool","reason_code":"x"}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];

  await streamChatSse(
    '/api/v1/chats/c1/messages',
    {},
    (event) => events.push(event),
    new AbortController().signal,
  );

  expect(events).toEqual([{ type: 'error', detail: 'Malformed route_selected frame' }]);
});

test('a non-OK response emits its problem detail as a terminal error', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify({ detail: 'workspace access denied' }), {
        status: 403,
        headers: { 'content-type': 'application/problem+json' },
      }),
    ),
  );
  const events: ChatSseEvent[] = [];

  await streamChatSse(
    '/api/v1/chats/c1/messages',
    { content: 'hi' },
    (event) => events.push(event),
    new AbortController().signal,
  );

  expect(events).toEqual([{ type: 'error', detail: 'workspace access denied' }]);
});

test('malformed data emits an error while subsequent frames remain readable', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      sseResponse([
        'event: token\ndata: not-json\n\n',
        'event: token\ndata: {"delta":"ok"}\n\n',
      ]),
    ),
  );
  const events: ChatSseEvent[] = [];

  await streamChatSse(
    '/api/v1/chats/c1/messages',
    {},
    (event) => events.push(event),
    new AbortController().signal,
  );

  expect(events.map((event) => event.type)).toEqual(['error', 'token']);
});

test('a backend error frame preserves its detail', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => sseResponse(['event: error\ndata: {"detail":"model unavailable"}\n\n'])),
  );
  const events: ChatSseEvent[] = [];

  await streamChatSse(
    '/api/v1/chats/c1/messages',
    {},
    (event) => events.push(event),
    new AbortController().signal,
  );

  expect(events).toEqual([{ type: 'error', detail: 'model unavailable' }]);
});
