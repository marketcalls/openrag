import { fireEvent, render, screen } from '@testing-library/react';
import { vi } from 'vitest';

import { RouteErrorPage } from './route-error-page';

test('offers a branded reload action without exposing exception details', () => {
  const reload = vi.fn();

  render(<RouteErrorPage reload={reload} />);

  expect(screen.getByText('OpenRAG needs a fresh connection')).toBeInTheDocument();
  expect(screen.queryByText(/stack|TypeError|chat-page/i)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Reload OpenRAG' }));
  expect(reload).toHaveBeenCalledOnce();
});
