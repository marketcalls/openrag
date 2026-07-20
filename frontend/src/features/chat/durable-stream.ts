import { authFetch } from '@/api/client';
import type { ChatRoute, CitationRef, DoneInfo, SourceRef } from '@/api/types';
import { createSseParser, type SseMessage } from '@/lib/sse';

import type { ChatSseEvent } from './stream';

interface AcceptedRun {
  run_id: string;
  events_url: string;
}

interface DurableEnvelope {
  event_type: string;
  run_id: string;
  payload: Record<string, unknown>;
}

const ROUTES = new Set<ChatRoute>(['direct', 'conversation', 'rag', 'analytics', 'clarify']);

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

function envelope(message: SseMessage, runId: string): DurableEnvelope | null {
  try {
    const value = objectValue(JSON.parse(message.data));
    const payload = objectValue(value?.payload);
    if (value?.run_id !== runId || value.event_type !== message.event || !payload) return null;
    return { event_type: message.event, run_id: runId, payload };
  } catch {
    return null;
  }
}

function safeSources(value: unknown): SourceRef[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((raw) => {
    const source = objectValue(raw);
    if (
      typeof source?.marker !== 'number' ||
      typeof source.document_id !== 'string' ||
      typeof source.filename !== 'string' ||
      typeof source.page !== 'number' ||
      typeof source.score !== 'number'
    ) {
      return [];
    }
    return [
      {
        ...(source as unknown as SourceRef),
        chunk_index: typeof source.chunk_index === 'number' ? source.chunk_index : 0,
        snippet: '',
      },
    ];
  });
}

async function problemDetail(response: Response): Promise<string> {
  try {
    const value = objectValue(await response.json());
    if (typeof value?.detail === 'string') return value.detail;
  } catch {
    // Use the bounded status fallback.
  }
  return `Request failed (${response.status})`;
}

export async function acceptDurableRun(
  chatId: string,
  body: { content: string; parent_message_id?: string | null; model_id?: string },
  signal: AbortSignal,
): Promise<AcceptedRun> {
  const response = await authFetch(
    new Request(new URL(`/api/v1/chats/${chatId}/runs`, window.location.origin), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ ...body, client_request_id: crypto.randomUUID() }),
      signal,
    }),
  );
  if (!response.ok) throw new Error(await problemDetail(response));
  const value = objectValue(await response.json());
  if (typeof value?.run_id !== 'string' || typeof value.events_url !== 'string') {
    throw new Error('Invalid run acceptance response');
  }
  return { run_id: value.run_id, events_url: value.events_url };
}

export async function streamDurableRun(
  accepted: AcceptedRun,
  onEvent: (event: ChatSseEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let lastEventId: string | null = null;
  let message: { id: string; noAnswer: boolean; citations: CitationRef[] } | null = null;
  let usage = { prompt_tokens: 0, completion_tokens: 0 };
  let reconnects = 0;

  while (!signal.aborted && reconnects < 4) {
    const headers = new Headers({ accept: 'text/event-stream' });
    if (lastEventId) headers.set('Last-Event-ID', lastEventId);
    let response: Response;
    try {
      response = await authFetch(
        new Request(new URL(accepted.events_url, window.location.origin), {
          method: 'GET',
          headers,
          credentials: 'include',
          signal,
        }),
      );
    } catch {
      if (signal.aborted) return;
      reconnects += 1;
      continue;
    }
    if (!response.ok || !response.body) {
      onEvent({ type: 'error', detail: await problemDetail(response) });
      return;
    }

    let terminal = false;
    const parser = createSseParser((frame) => {
      if (frame.id) lastEventId = frame.id;
      const event = envelope(frame, accepted.run_id);
      if (!event) return;
      const data = event.payload;
      switch (event.event_type) {
        case 'route.selected':
          if (typeof data.route === 'string' && ROUTES.has(data.route as ChatRoute)) {
            onEvent({
              type: 'route_selected',
              route: data.route as ChatRoute,
              reasonCode: typeof data.reason_code === 'string' ? data.reason_code : 'durable',
            });
          }
          break;
        case 'retrieval.started':
          onEvent({ type: 'retrieval_started' });
          break;
        case 'retrieval.sources':
          onEvent({ type: 'sources', sources: safeSources(data.sources) });
          break;
        case 'message.delta':
          if (typeof data.delta === 'string') onEvent({ type: 'token', delta: data.delta });
          break;
        case 'message.completed':
          if (typeof data.message_id === 'string') {
            message = {
              id: data.message_id,
              noAnswer: data.no_answer === true,
              citations: Array.isArray(data.citations) ? (data.citations as CitationRef[]) : [],
            };
            onEvent({ type: 'citations', citations: message.citations });
          }
          break;
        case 'usage.updated':
          if (
            typeof data.prompt_tokens === 'number' &&
            typeof data.completion_tokens === 'number'
          ) {
            usage = {
              prompt_tokens: data.prompt_tokens,
              completion_tokens: data.completion_tokens,
            };
          }
          break;
        case 'run.completed':
          if (message) {
            const done: DoneInfo = {
              message_id: message.id,
              prompt_tokens: usage.prompt_tokens,
              completion_tokens: usage.completion_tokens,
              no_answer: message.noAnswer,
            };
            onEvent({ type: 'done', done });
          } else {
            onEvent({ type: 'error', detail: 'Run completed without a message' });
          }
          terminal = true;
          break;
        case 'run.failed':
          onEvent({
            type: 'error',
            detail: typeof data.error_code === 'string' ? data.error_code : 'Run failed',
          });
          terminal = true;
          break;
        case 'run.cancelled':
          terminal = true;
          break;
      }
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    try {
      for (;;) {
        const chunk = await reader.read();
        if (chunk.done) break;
        parser.feed(decoder.decode(chunk.value, { stream: true }));
      }
      parser.feed(decoder.decode());
      parser.flush();
    } catch {
      if (signal.aborted) return;
    }
    if (terminal) return;
    reconnects += 1;
  }
  if (!signal.aborted) onEvent({ type: 'error', detail: 'Stream interrupted' });
}

export async function cancelDurableRun(runId: string): Promise<void> {
  await authFetch(
    new Request(new URL(`/api/v1/runs/${runId}/cancel`, window.location.origin), {
      method: 'POST',
      credentials: 'include',
    }),
  );
}
