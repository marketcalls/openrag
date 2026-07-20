import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';

import type { ChatRoute, CitationRef, SourceRef } from '@/api/types';

import {
  acceptDurableRegeneration,
  acceptDurableRun,
  cancelDurableRun,
  streamDurableRun,
} from './durable-stream';
import type { ChatSseEvent } from './stream';

export type StreamStatus =
  'idle' | 'routing' | 'retrieving' | 'generating' | 'streaming' | 'done' | 'error';

export interface ChatStreamState {
  status: StreamStatus;
  route: ChatRoute | null;
  text: string;
  sources: SourceRef[];
  citations: CitationRef[];
  noAnswer: boolean;
  errorDetail: string | null;
  pendingUserContent: string | null;
  doneMessageId: string | null;
}

const IDLE: ChatStreamState = {
  status: 'idle',
  route: null,
  text: '',
  sources: [],
  citations: [],
  noAnswer: false,
  errorDetail: null,
  pendingUserContent: null,
  doneMessageId: null,
};

function reduceStream(state: ChatStreamState, event: ChatSseEvent): ChatStreamState {
  switch (event.type) {
    case 'route_selected':
      return {
        ...state,
        route: event.route,
        status:
          event.route === 'direct' || event.route === 'conversation' || event.route === 'clarify'
            ? 'generating'
            : 'retrieving',
      };
    case 'retrieval_started':
      return { ...state, status: 'retrieving' };
    case 'sources':
      return { ...state, sources: event.sources };
    case 'token':
      return { ...state, status: 'streaming', text: state.text + event.delta };
    case 'citations':
      return { ...state, citations: event.citations };
    case 'done':
      return {
        ...state,
        status: 'done',
        noAnswer: event.done.no_answer,
        doneMessageId: event.done.message_id,
      };
    case 'error':
      return { ...state, status: 'error', errorDetail: event.detail };
  }
}

export function useChatStream(chatId: string | null) {
  const queryClient = useQueryClient();
  const [state, setState] = useState<ChatStreamState>(IDLE);
  const abortController = useRef<AbortController | null>(null);
  const activeRunId = useRef<string | null>(null);

  const send = useCallback(
    (content: string, parentMessageId?: string | null, modelId?: string | null) => {
      if (!chatId) return;
      abortController.current?.abort();
      const controller = new AbortController();
      abortController.current = controller;
      activeRunId.current = null;
      setState({ ...IDLE, status: 'routing', pendingUserContent: content });
      void (async () => {
        try {
          const accepted = await acceptDurableRun(
            chatId,
            {
              content,
              ...(parentMessageId !== undefined ? { parent_message_id: parentMessageId } : {}),
              ...(modelId ? { model_id: modelId } : {}),
            },
            controller.signal,
          );
          activeRunId.current = accepted.run_id;
          await streamDurableRun(
            accepted,
            (event) => {
              setState((current) => reduceStream(current, event));
              if (event.type === 'done') {
                activeRunId.current = null;
                void queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
                void queryClient.invalidateQueries({ queryKey: ['chats'] });
              }
            },
            controller.signal,
          );
        } catch (error) {
          if (!controller.signal.aborted) {
            setState((current) => ({
              ...current,
              status: 'error',
              errorDetail: error instanceof Error ? error.message : 'Request failed',
            }));
          }
        }
      })();
    },
    [chatId, queryClient],
  );

  const regenerate = useCallback(
    (messageId: string) => {
      if (!chatId) return;
      abortController.current?.abort();
      const controller = new AbortController();
      abortController.current = controller;
      activeRunId.current = null;
      setState({ ...IDLE, status: 'routing', pendingUserContent: null });
      void (async () => {
        try {
          const accepted = await acceptDurableRegeneration(messageId, null, controller.signal);
          activeRunId.current = accepted.run_id;
          await streamDurableRun(
            accepted,
            (event) => {
              setState((current) => reduceStream(current, event));
              if (event.type === 'done') {
                activeRunId.current = null;
                void queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
                void queryClient.invalidateQueries({ queryKey: ['chats'] });
              }
            },
            controller.signal,
          );
        } catch (error) {
          if (!controller.signal.aborted) {
            setState((current) => ({
              ...current,
              status: 'error',
              errorDetail: error instanceof Error ? error.message : 'Request failed',
            }));
          }
        }
      })();
    },
    [chatId, queryClient],
  );

  const abort = useCallback(() => {
    const runId = activeRunId.current;
    activeRunId.current = null;
    if (runId) void cancelDurableRun(runId);
    abortController.current?.abort();
    setState((current) => ({ ...current, status: 'idle' }));
  }, []);
  const reset = useCallback(() => setState({ ...IDLE }), []);

  return { ...state, send, regenerate, abort, reset };
}
