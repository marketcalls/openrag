import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useRef, useState } from 'react';

import type { ChatRoute, CitationRef, SourceRef } from '@/api/types';

import { streamChatSse, type ChatSseEvent } from './stream';

export type StreamStatus =
  | 'idle'
  | 'routing'
  | 'retrieving'
  | 'generating'
  | 'streaming'
  | 'done'
  | 'error';

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
          event.route === 'direct' ||
          event.route === 'conversation' ||
          event.route === 'clarify'
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

  const run = useCallback(
    (url: string, body: unknown, pendingUserContent: string | null) => {
      abortController.current?.abort();
      const controller = new AbortController();
      abortController.current = controller;
      setState({ ...IDLE, status: 'routing', pendingUserContent });
      void streamChatSse(
        url,
        body,
        (event) => {
          setState((current) => reduceStream(current, event));
          if (event.type === 'done') {
            void queryClient.invalidateQueries({ queryKey: ['chat', chatId] });
            void queryClient.invalidateQueries({ queryKey: ['chats'] });
          }
        },
        controller.signal,
      );
    },
    [chatId, queryClient],
  );

  const send = useCallback(
    (content: string, parentMessageId?: string | null, modelId?: string | null) => {
      if (!chatId) return;
      run(
        `/api/v1/chats/${chatId}/messages`,
        {
          content,
          ...(parentMessageId !== undefined ? { parent_message_id: parentMessageId } : {}),
          ...(modelId ? { model_id: modelId } : {}),
        },
        content,
      );
    },
    [chatId, run],
  );

  const regenerate = useCallback(
    (messageId: string) => run(`/api/v1/messages/${messageId}/regenerate`, {}, null),
    [run],
  );

  const abort = useCallback(() => {
    abortController.current?.abort();
    setState((current) => ({ ...current, status: 'idle' }));
  }, []);
  const reset = useCallback(() => setState({ ...IDLE }), []);

  return { ...state, send, regenerate, abort, reset };
}
