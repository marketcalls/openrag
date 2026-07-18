import { authFetch } from '@/api/client';
import type { CitationRef, DoneInfo, SourceRef } from '@/api/types';
import { createSseParser, type SseMessage } from '@/lib/sse';

export type ChatSseEvent =
  | { type: 'retrieval_started' }
  | { type: 'sources'; sources: SourceRef[] }
  | { type: 'token'; delta: string }
  | { type: 'citations'; citations: CitationRef[] }
  | { type: 'done'; done: DoneInfo }
  | { type: 'error'; detail: string };

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : null;
}

function toEvent(message: SseMessage): ChatSseEvent {
  try {
    const data = objectValue(JSON.parse(message.data));
    if (!data) throw new Error('event payload is not an object');
    switch (message.event) {
      case 'retrieval_started':
        return { type: 'retrieval_started' };
      case 'sources':
        if (!Array.isArray(data.sources)) throw new Error('sources missing');
        return { type: 'sources', sources: data.sources as SourceRef[] };
      case 'token':
        if (typeof data.delta !== 'string') throw new Error('delta missing');
        return { type: 'token', delta: data.delta };
      case 'citations':
        if (!Array.isArray(data.citations)) throw new Error('citations missing');
        return { type: 'citations', citations: data.citations as CitationRef[] };
      case 'done':
        if (
          typeof data.message_id !== 'string' ||
          typeof data.prompt_tokens !== 'number' ||
          typeof data.completion_tokens !== 'number' ||
          typeof data.no_answer !== 'boolean'
        ) {
          throw new Error('done fields missing');
        }
        return { type: 'done', done: data as unknown as DoneInfo };
      case 'error':
        if (typeof data.detail !== 'string') throw new Error('detail missing');
        return { type: 'error', detail: data.detail };
      default:
        return { type: 'error', detail: `Unknown event: ${message.event}` };
    }
  } catch {
    return { type: 'error', detail: `Malformed ${message.event} frame` };
  }
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === 'AbortError';
}

export async function streamChatSse(
  url: string,
  body: unknown,
  onEvent: (event: ChatSseEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  let response: Response;
  try {
    response = await authFetch(
      new Request(new URL(url, window.location.origin), {
        method: 'POST',
        headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
        body: JSON.stringify(body),
        credentials: 'include',
        signal,
      }),
    );
  } catch (error) {
    if (isAbortError(error)) return;
    onEvent({ type: 'error', detail: 'Network error' });
    return;
  }

  if (!response.ok || !response.body) {
    let detail = `Request failed (${response.status})`;
    try {
      const problem = objectValue(await response.json());
      if (typeof problem?.detail === 'string') detail = problem.detail;
    } catch {
      // Preserve the status-based fallback for non-JSON upstream responses.
    }
    onEvent({ type: 'error', detail });
    return;
  }

  const parser = createSseParser((message) => onEvent(toEvent(message)));
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.feed(decoder.decode(value, { stream: true }));
    }
    parser.feed(decoder.decode());
    parser.flush();
  } catch (error) {
    if (isAbortError(error)) return;
    onEvent({ type: 'error', detail: 'Stream interrupted' });
  }
}
