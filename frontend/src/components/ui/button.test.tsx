import { render, screen } from '@testing-library/react';

import { Button } from './button';

test('primary variant uses inverted primary tokens', () => {
  render(<Button variant="primary">Save</Button>);

  const button = screen.getByRole('button', { name: 'Save' });
  expect(button.className).toContain('bg-primary');
  expect(button.className).toContain('text-primary-foreground');
});

test('defaults to secondary and supports disabled state', () => {
  render(<Button disabled>Cancel</Button>);

  const button = screen.getByRole('button', { name: 'Cancel' });
  expect(button).toBeDisabled();
  expect(button.className).toContain('border-line');
});
