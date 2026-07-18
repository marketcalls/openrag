import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import type { CitationRef, SourceRef } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Spinner } from '@/components/ui/spinner';
import { useModels } from '@/features/models/queries';
import { useWorkspaces } from '@/features/workspaces/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

import { AssistantMessage } from './assistant-message';
import { ChatInput } from './chat-input';
import { ModelSelector } from './model-selector';
import { useChat, useCreateChat } from './queries';
import { StreamingMessage } from './streaming-message';
import { activeLeafId, treeContainsMessage } from './tree';
import { UsageMeter } from './usage-meter';
import { UserMessage } from './user-message';
import { useChatStream } from './use-chat-stream';
import { useTreeSelection } from './use-tree-selection';

function historicalSources(citations: CitationRef[]): SourceRef[] {
  return citations.map((citation) => ({
    marker: citation.marker,
    document_id: citation.document_id,
    filename: `Document ${citation.document_id.slice(0, 8)}`,
    page: citation.page,
    chunk_index: Number(citation.chunk_ref.split(':').at(-1)) || 0,
    score: citation.score,
    snippet: '',
  }));
}

export function ChatPage() {
  const { chatId = null } = useParams<{ chatId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const { workspaceId } = useWorkspace();
  const { data: workspaces } = useWorkspaces();
  const { data: models } = useModels();
  const chatQuery = useChat(chatId);
  const createChat = useCreateChat();
  const stream = useChatStream(chatId);
  const { path } = useTreeSelection(chatQuery.data?.messages);
  const [modelId, setModelId] = useState<string | null>(null);
  const endOfThread = useRef<HTMLDivElement>(null);

  const workspace = workspaces?.find((item) => item.id === workspaceId);
  const effectiveModelId = modelId ?? workspace?.default_model_id ?? models?.[0]?.id ?? null;

  const initialMessage = (location.state as { initialMessage?: string } | null)?.initialMessage;
  const sentHandoff = useRef<string | null>(null);
  useEffect(() => {
    if (!chatId || !initialMessage) return;
    const handoffKey = `${chatId}:${initialMessage}`;
    if (sentHandoff.current === handoffKey) return;
    sentHandoff.current = handoffKey;
    stream.send(initialMessage, null, effectiveModelId);
    navigate(location.pathname, { replace: true, state: null });
  }, [chatId, effectiveModelId, initialMessage, location.pathname, navigate, stream]);

  const streamedInTree = useMemo(
    () =>
      stream.doneMessageId !== null &&
      treeContainsMessage(chatQuery.data?.messages ?? [], stream.doneMessageId),
    [chatQuery.data?.messages, stream.doneMessageId],
  );
  useEffect(() => {
    if (streamedInTree) stream.reset();
  }, [stream, streamedInTree]);

  useEffect(() => {
    endOfThread.current?.scrollIntoView({ block: 'end' });
  }, [path.length, stream.status, stream.text]);

  const onSend = (content: string) => {
    if (chatId) {
      stream.send(content, activeLeafId(path), effectiveModelId);
      return;
    }
    if (!workspaceId) return;
    createChat.mutate(
      { workspace_id: workspaceId },
      {
        onSuccess: (chat) =>
          navigate(`/chat/${chat.id}`, { state: { initialMessage: content } }),
      },
    );
  };

  const busy = stream.status === 'retrieving' || stream.status === 'streaming';
  const showStream = stream.status !== 'idle' && !streamedInTree;

  return (
    <>
      <TopBar
        title={chatQuery.data?.title || 'New chat'}
        actions={
          <>
            <UsageMeter />
            <ModelSelector
              models={models ?? []}
              value={effectiveModelId}
              onChange={setModelId}
            />
          </>
        }
      />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-thread space-y-5 px-4 py-6">
          {chatId && chatQuery.isPending ? <Spinner label="Loading chat…" /> : null}
          {chatQuery.isError ? (
            <p role="alert" className="rounded-md bg-danger-soft px-3 py-2 text-danger">
              Unable to load this chat.
            </p>
          ) : null}
          {path.map((entry) =>
            entry.message.role === 'user' ? (
              <UserMessage key={entry.message.id} content={entry.message.content} />
            ) : (
              <AssistantMessage
                key={entry.message.id}
                content={entry.message.content}
                sources={historicalSources(entry.message.citations)}
              />
            ),
          )}
          {showStream ? <StreamingMessage stream={stream} /> : null}
          {!chatId && path.length === 0 && stream.status === 'idle' ? (
            <p className="pt-16 text-center text-[15px] text-secondary">
              Ask a question about the documents in this workspace.
            </p>
          ) : null}
          {!workspaceId ? (
            <p className="text-center text-[13px] text-warning">
              Create or select a workspace before starting a chat.
            </p>
          ) : null}
          <div ref={endOfThread} />
        </div>
      </div>
      <ChatInput
        onSend={onSend}
        disabled={busy || createChat.isPending || !workspaceId || !effectiveModelId}
        placeholder={!effectiveModelId ? 'Configure a model to start chatting' : undefined}
      />
    </>
  );
}
