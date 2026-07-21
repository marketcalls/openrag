import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';

import { UsageMeter } from './usage-meter';

function renderMeter(body: unknown) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ),
  );
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <UsageMeter />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test('renders real used and allocated tokens with the reset date', async () => {
  renderMeter({
    used_tokens: 12_300,
    allocated_tokens: 100_000,
    org_used_tokens: 12_300,
    org_allocated_tokens: 1_000_000,
    resets_at: '2026-08-01T00:00:00',
    warning: false,
    blocked: false,
  });

  const meter = await screen.findByText('12.3K / 100K tokens · resets Aug 1');
  expect(meter).toHaveClass('text-muted');
  expect(screen.queryByText(/sample/i)).not.toBeInTheDocument();
});

test('shows usage without a fake denominator when no quota is configured', async () => {
  renderMeter({
    used_tokens: 49,
    allocated_tokens: null,
    org_used_tokens: 49,
    org_allocated_tokens: null,
    resets_at: '2026-08-01T00:00:00',
    warning: false,
    blocked: false,
  });

  expect(await screen.findByText('49 tokens this month')).not.toHaveTextContent('/');
});

test('highlights warning and blocked usage', async () => {
  renderMeter({
    used_tokens: 100_000,
    allocated_tokens: 100_000,
    org_used_tokens: 100_000,
    org_allocated_tokens: 1_000_000,
    resets_at: '2026-08-01T00:00:00',
    warning: true,
    blocked: true,
  });

  expect(await screen.findByText('100K / 100K tokens · resets Aug 1')).toHaveClass(
    'text-danger',
  );
});
