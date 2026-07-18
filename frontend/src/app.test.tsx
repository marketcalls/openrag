import { render, screen } from '@testing-library/react';

import { App } from './app';

test('renders the OpenRAG entry point', () => {
  render(<App />);

  expect(screen.getByRole('heading', { name: 'OpenRAG' })).toBeInTheDocument();
});
