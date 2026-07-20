import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';

import type {
  CitationRef,
  ReasoningEffort,
  SourceRef,
} from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Spinner } from '@/components/ui/spinner';
import { useModels } from '@/features/models/queries';
import { useWorkspaces } from '@/features/workspaces/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

import { AssistantMessage } from './assistant-message';
import { ChatInput } from './chat-input';
import { EditMessageForm } from './edit-message-form';
import { MessageActions } from './message-actions';
import { ModelSelector } from './model-selector';
import { useChat, useCreateChat } from './queries';
import { StreamingMessage } from './streaming-message';
import { activeLeafId, treeContainsMessage } from './tree';
import { UsageMeter } from './usage-meter';
import { UserMessage } from './user-message';
import { useChatStream } from './use-chat-stream';
import { useTreeSelection } from './use-tree-selection';
import { parseHistoricalAnalyticsArtifacts } from './analytics/contract';

function historicalSources(citations: CitationRef[]): SourceRef[] {
  return citations.map((citation) => ({
    marker: citation.marker,
    document_id: citation.document_id,
    filename: citation.document_name ?? `Document ${citation.document_id.slice(0, 8)}`,
    page: citation.page,
    chunk_index: Number(citation.chunk_ref.split(':').at(-1)) || 0,
    score: citation.score,
    snippet: '',
    document_version_id: citation.document_version_id,
    evidence_span_id: citation.evidence_span_id,
    version_label: citation.version_label,
    section_label: citation.section_label,
    section_path: citation.section_path,
    locator_kind: citation.locator_kind,
    locator_label: citation.locator_label,
    content_hash: citation.content_hash,
    dense_score: citation.dense_score,
    sparse_score: citation.sparse_score,
    fused_score: citation.fused_score,
    rerank_score: citation.rerank_score,
  }));
}

export function ChatPage() {
  const { chatId = null } = useParams<{ chatId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const { workspaceId } = useWorkspace();
  const { data: workspaces } = useWorkspaces();
  const modelsQuery = useModels();
  const models = modelsQuery.data;
  const chatQuery = useChat(chatId);
  const createChat = useCreateChat();
  const stream = useChatStream(chatId);
  const { path, select, reset: resetSelection } = useTreeSelection(chatQuery.data?.messages);
  const [modelId, setModelId] = useState<string | null>(null);
  const [reasoningOverride, setReasoningOverride] = useState<ReasoningEffort | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const endOfThread = useRef<HTMLDivElement>(null);

  const workspace = workspaces?.find((item) => item.id === workspaceId);
  const effectiveModelId = modelId ?? workspace?.default_model_id ?? models?.[0]?.id ?? null;
  const effectiveModel = models?.find((model) => model.id === effectiveModelId);
  const effectiveReasoningEffort: ReasoningEffort = effectiveModel?.supports_reasoning
    ? (reasoningOverride ?? effectiveModel.default_reasoning_effort)
    : 'off';

  const handoff = location.state as
    | {
        initialMessage?: string;
        initialModelId?: string | null;
        initialReasoningEffort?: ReasoningEffort;
      }
    | null;
  const initialMessage = handoff?.initialMessage;
  const sentHandoff = useRef<string | null>(null);
  useEffect(() => {
    if (!chatId || !initialMessage) return;
    const handoffKey = `${chatId}:${initialMessage}`;
    if (sentHandoff.current === handoffKey) return;
    sentHandoff.current = handoffKey;
    stream.send(
      initialMessage,
      null,
      handoff?.initialModelId ?? effectiveModelId,
      handoff?.initialReasoningEffort ?? effectiveReasoningEffort,
    );
    navigate(location.pathname, { replace: true, state: null });
  }, [
    chatId,
    effectiveModelId,
    effectiveReasoningEffort,
    handoff?.initialModelId,
    handoff?.initialReasoningEffort,
    initialMessage,
    location.pathname,
    navigate,
    stream,
  ]);

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
      stream.send(content, activeLeafId(path), effectiveModelId, effectiveReasoningEffort);
      return;
    }
    if (!workspaceId) return;
    createChat.mutate(
      { workspace_id: workspaceId },
      {
        onSuccess: (chat) =>
          navigate(`/chat/${chat.id}`, {
            state: {
              initialMessage: content,
              initialModelId: effectiveModelId,
              initialReasoningEffort: effectiveReasoningEffort,
            },
          }),
      },
    );
  };

  const busy = !['idle', 'error'].includes(stream.status);
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
              loading={modelsQuery.isPending}
              error={modelsQuery.isError}
              onChange={(nextModelId) => {
                setModelId(nextModelId);
                setReasoningOverride(null);
              }}
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
          {path.map((entry) => {
            const message = entry.message;
            if (message.role === 'user') {
              if (editingId === message.id) {
                return (
                  <EditMessageForm
                    key={message.id}
                    initial={message.content}
                    onCancel={() => setEditingId(null)}
                    onSend={(content) => {
                      setEditingId(null);
                      resetSelection();
                      stream.send(
                        content,
                        message.parent_message_id,
                        effectiveModelId,
                        effectiveReasoningEffort,
                      );
                    }}
                  />
                );
              }
              return (
                <UserMessage
                  key={message.id}
                  content={message.content}
                  footer={
                    <MessageActions
                      entry={entry}
                      disabled={busy}
                      onSelectSibling={select}
                      onEdit={() => setEditingId(message.id)}
                    />
                  }
                />
              );
            }
            return (
              <AssistantMessage
                key={message.id}
                content={message.content}
                sources={historicalSources(message.citations)}
                artifact={parseHistoricalAnalyticsArtifacts(message.artifacts)}
                onFollowup={onSend}
                followupDisabled={busy}
                footer={
                  <MessageActions
                    entry={entry}
                    disabled={busy}
                    onSelectSibling={select}
                    onRegenerate={() => {
                      resetSelection();
                      stream.regenerate(
                        message.id,
                        effectiveModelId,
                        effectiveReasoningEffort,
                      );
                    }}
                  />
                }
              />
            );
          })}
          {showStream ? (
            <StreamingMessage
              stream={stream}
              onFollowup={onSend}
              followupDisabled={busy}
            />
          ) : null}
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
        supportsReasoning={effectiveModel?.supports_reasoning ?? false}
        reasoningEffort={effectiveReasoningEffort}
        onReasoningEffortChange={setReasoningOverride}
      />
    </>
  );
}
