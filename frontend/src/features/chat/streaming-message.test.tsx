import { render, screen } from '@testing-library/react';

import { StreamingMessage } from './streaming-message';
import type { ChatStreamState } from './use-chat-stream';

const BASE: ChatStreamState = {
  status: 'planning',
  route: 'rag',
  text: '',
  sources: [],
  citations: [],
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
