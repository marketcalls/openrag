import { Spinner } from '@/components/ui/spinner';

import { AssistantMessage } from './assistant-message';
import type { ChatStreamState } from './use-chat-stream';
import { UserMessage } from './user-message';

export function StreamingMessage({ stream }: { stream: ChatStreamState }) {
  const routeLabel =
    stream.route === 'direct'
      ? 'Direct response'
      : stream.route === 'conversation'
        ? 'Conversation context'
        : stream.route === 'rag'
          ? 'Workspace documents'
          : stream.route === 'analytics'
            ? 'Grounded analysis'
            : stream.route === 'clarify'
              ? 'Clarification'
              : null;
  return (
    <>
      {stream.pendingUserContent ? <UserMessage content={stream.pendingUserContent} /> : null}
      {stream.status === 'routing' ? <Spinner label="Routing request…" /> : null}
      {stream.status === 'planning' ? (
        <Spinner label={stream.agentProgress ?? 'Planning evidence search…'} />
      ) : null}
      {stream.status === 'retrieving' ? (
        <Spinner label={stream.agentProgress ?? 'Searching documents…'} />
      ) : null}
      {stream.status === 'generating' ? (
        <Spinner label={stream.agentProgress ?? 'Generating response…'} />
      ) : null}
      {stream.status === 'streaming' || stream.status === 'done' ? (
        <div>
          {routeLabel ? (
            <p className="mb-2 text-[11px] font-medium uppercase tracking-wide text-muted">
              {routeLabel}
            </p>
          ) : null}
          <AssistantMessage
            content={stream.text}
            sources={stream.sources}
            noAnswer={stream.noAnswer}
          />
        </div>
      ) : null}
      {stream.status === 'error' ? (
        <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-[13px] text-danger">
          {stream.errorDetail ?? 'Something went wrong.'}
        </p>
      ) : null}
    </>
  );
}
