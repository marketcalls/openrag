import type { ChatStreamState } from './use-chat-stream';
import { reduceStream } from './use-chat-stream';

const ARTIFACT = {
  schema_version: 'analytics.v1' as const,
  title: 'Revenue dashboard',
  subtitle: null,
  kpis: [],
  blocks: [
    {
      kind: 'explainer' as const,
      title: 'Summary',
      body_markdown: 'Revenue increased [1].',
      source_markers: [1],
    },
  ],
  suggested_followups: [],
};

const STATE: ChatStreamState = {
  status: 'streaming',
  route: 'analytics',
  text: 'Grounded answer [1].',
  sources: [],
  citations: [],
  artifact: null,
  noAnswer: false,
  errorDetail: null,
  pendingUserContent: 'Show revenue',
  doneMessageId: null,
  agentProgress: null,
};

test('stores a provisional artifact and preserves it only through successful completion', () => {
  const withArtifact = reduceStream(STATE, { type: 'artifact', artifact: ARTIFACT });
  expect(withArtifact.artifact).toEqual(ARTIFACT);

  const completed = reduceStream(withArtifact, {
    type: 'done',
    done: {
      message_id: 'message-1',
      prompt_tokens: 10,
      completion_tokens: 5,
      no_answer: false,
    },
  });
  expect(completed.artifact).toEqual(ARTIFACT);
});

test('clears an uncommitted provisional artifact when the run fails', () => {
  const withArtifact = { ...STATE, artifact: ARTIFACT };

  expect(
    reduceStream(withArtifact, { type: 'error', detail: 'Run failed' }).artifact,
  ).toBeNull();
});
