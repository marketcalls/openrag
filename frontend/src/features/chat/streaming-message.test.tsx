import { render, screen } from '@testing-library/react';

import { StreamingMessage } from './streaming-message';
import type { ChatStreamState } from './use-chat-stream';

const BASE: ChatStreamState = {
  status: 'planning',
  route: 'rag',
  text: '',
  sources: [],
  citations: [],
  artifact: null,
  noAnswer: false,
  errorDetail: null,
  pendingUserContent: null,
  doneMessageId: null,
  agentProgress: 'Planning evidence search…',
};

test('shows bounded agent progress without exposing prompts or reasoning', () => {
  render(<StreamingMessage stream={BASE} />);

  expect(screen.getByText('Planning evidence search…')).toBeInTheDocument();
  expect(screen.queryByText(/reasoning/i)).not.toBeInTheDocument();
});

test('renders a validated live artifact and sends its follow-up', () => {
  const onFollowup = vi.fn();
  render(
    <StreamingMessage
      stream={{
        ...BASE,
        status: 'done',
        route: 'analytics',
        text: 'Grounded answer [1].',
        artifact: {
          schema_version: 'analytics.v1',
          title: 'Revenue dashboard',
          subtitle: null,
          kpis: [],
          blocks: [
            {
              kind: 'explainer',
              title: 'Summary',
              body_markdown: 'Revenue increased [1].',
              source_markers: [1],
            },
          ],
          suggested_followups: ['Break this down'],
        },
      }}
      onFollowup={onFollowup}
    />,
  );

  expect(screen.getByRole('heading', { name: 'Revenue dashboard' })).toBeVisible();
  screen.getByRole('button', { name: 'Ask: Break this down' }).click();
  expect(onFollowup).toHaveBeenCalledWith('Break this down');
});
