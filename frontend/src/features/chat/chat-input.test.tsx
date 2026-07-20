import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { ChatInput } from './chat-input';

test('Enter sends and clears while Shift+Enter inserts a newline', async () => {
  const onSend = vi.fn();
  const user = userEvent.setup();
  render(<ChatInput onSend={onSend} disabled={false} />);
  const textbox = screen.getByRole('textbox', { name: 'Message' });
  await user.type(textbox, 'hello');
  await user.keyboard('{Enter}');
  expect(onSend).toHaveBeenCalledWith('hello');
  expect(textbox).toHaveValue('');
  await user.type(textbox, 'a{Shift>}{Enter}{/Shift}b');
  expect(textbox).toHaveValue('a\nb');
  expect(onSend).toHaveBeenCalledTimes(1);
});

test('whitespace-only content is not sent and disabled state blocks sending', async () => {
  const onSend = vi.fn();
  const user = userEvent.setup();
  const { rerender } = render(<ChatInput onSend={onSend} disabled={false} />);
  await user.type(screen.getByRole('textbox', { name: 'Message' }), '   {Enter}');
  expect(onSend).not.toHaveBeenCalled();
  rerender(<ChatInput onSend={onSend} disabled />);
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
});

test('shows bounded reasoning controls only for capable models', async () => {
  const onReasoningEffortChange = vi.fn();
  const { rerender } = render(
    <ChatInput
      onSend={vi.fn()}
      disabled={false}
      supportsReasoning={false}
      reasoningEffort="off"
      onReasoningEffortChange={onReasoningEffortChange}
    />,
  );
  expect(screen.queryByLabelText('Reasoning effort')).not.toBeInTheDocument();

  rerender(
    <ChatInput
      onSend={vi.fn()}
      disabled={false}
      supportsReasoning
      reasoningEffort="medium"
      onReasoningEffortChange={onReasoningEffortChange}
    />,
  );
  await userEvent.selectOptions(screen.getByLabelText('Reasoning effort'), 'high');
  expect(onReasoningEffortChange).toHaveBeenCalledWith('high');
});
