import { render, screen } from '@testing-library/react';

import { App } from './app';

afterEach(() => vi.unstubAllGlobals());

test('the public root renders the OpenRAG home page with login access', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 401 })));
  window.history.pushState({}, '', '/');
  render(<App />);
  expect(
    await screen.findByRole('heading', { name: /Ask your company/i }),
  ).toBeInTheDocument();
  expect(screen.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login');
});
