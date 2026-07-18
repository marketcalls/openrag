import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { EditMessageForm } from './edit-message-form';

test('prefills, edits, and sends trimmed content', async () => {
  const onSend = vi.fn();
  const user = userEvent.setup();
  render(<EditMessageForm initial="old question" onCancel={vi.fn()} onSend={onSend} />);
  const textbox = screen.getByRole('textbox', { name: 'Edit message' });
  expect(textbox).toHaveValue('old question');
  await user.clear(textbox);
  await user.type(textbox, '  new question  ');
  await user.click(screen.getByRole('button', { name: 'Send' }));
  expect(onSend).toHaveBeenCalledWith('new question');
});

test('Cancel and Escape both cancel while empty content cannot send', async () => {
  const onCancel = vi.fn();
  const onSend = vi.fn();
  const user = userEvent.setup();
  render(<EditMessageForm initial="x" onCancel={onCancel} onSend={onSend} />);
  const textbox = screen.getByRole('textbox', { name: 'Edit message' });
  await user.clear(textbox);
  expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: 'Cancel' }));
  await user.type(textbox, '{Escape}');
  expect(onCancel).toHaveBeenCalledTimes(2);
  expect(onSend).not.toHaveBeenCalled();
});
