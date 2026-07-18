import { render, screen } from '@testing-library/react';

import { StatusPill } from './status-pill';

test.each([
  ['success', 'bg-success-soft text-success'],
  ['accent', 'bg-accent-soft text-accent-on-soft'],
  ['danger', 'bg-danger-soft text-danger'],
  ['warning', 'bg-warning-soft text-warning'],
] as const)('tone %s applies a labeled soft status treatment', (tone, expected) => {
  render(<StatusPill tone={tone}>Indexed</StatusPill>);

  const pill = screen.getByText('Indexed');
  for (const className of expected.split(' ')) {
    expect(pill.className).toContain(className);
  }
  expect(pill.className).toContain('rounded-full');
});
