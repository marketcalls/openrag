import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { MessageOut } from '@/api/types';

import { MessageActions } from './message-actions';
import type { PathEntry } from './tree';

function entryFor(
  overrides: Partial<MessageOut>,
  siblings: string[] = ['m1'],
  position = 0,
): PathEntry {
  return {
    message: {
      id: 'm1',
      parent_message_id: 'p1',
      sibling_index: 0,
      role: 'user',
      content: 'the content',
      model_id: null,
      prompt_tokens: null,
      completion_tokens: null,
      created_at: '2026-07-18T00:00:00Z',
      citations: [],
      artifacts: [],
      children: [],
      ...overrides,
    },
    siblings,
    position,
  };
}

test('copy writes the message content to the clipboard', async () => {
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
  render(<MessageActions entry={entryFor({})} disabled={false} onSelectSibling={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Copy message' }));
  await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith('the content'));
});

test('edit and regenerate controls appear only when their handlers exist', () => {
  const { rerender } = render(
    <MessageActions
      entry={entryFor({})}
      disabled={false}
      onSelectSibling={vi.fn()}
      onEdit={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Edit message' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Regenerate response' })).not.toBeInTheDocument();
  rerender(
    <MessageActions
      entry={entryFor({ role: 'assistant' })}
      disabled={false}
      onSelectSibling={vi.fn()}
      onRegenerate={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Regenerate response' })).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Edit message' })).not.toBeInTheDocument();
});

test('sibling navigation displays position and selects by branch key', async () => {
  const onSelectSibling = vi.fn();
  const user = userEvent.setup();
  render(
    <MessageActions
      entry={entryFor({ id: 'm2', sibling_index: 1 }, ['m1', 'm2', 'm3'], 1)}
      disabled={false}
      onSelectSibling={onSelectSibling}
    />,
  );
  expect(screen.getByText('2/3')).toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: 'Previous version' }));
  expect(onSelectSibling).toHaveBeenCalledWith('p1', 'm1');
  await user.click(screen.getByRole('button', { name: 'Next version' }));
  expect(onSelectSibling).toHaveBeenCalledWith('p1', 'm3');
});

test('single siblings hide navigation and branch ends disable their arrow', () => {
  const { rerender } = render(
    <MessageActions entry={entryFor({})} disabled={false} onSelectSibling={vi.fn()} />,
  );
  expect(screen.queryByRole('button', { name: 'Previous version' })).not.toBeInTheDocument();
  rerender(
    <MessageActions
      entry={entryFor({}, ['m1', 'm2'], 0)}
      disabled={false}
      onSelectSibling={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Previous version' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Next version' })).toBeEnabled();
});
