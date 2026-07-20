import { render, screen } from '@testing-library/react';

import { ModelSelector } from './model-selector';

const noop = () => undefined;

test('distinguishes model loading, errors, and no ready model', () => {
  const { rerender } = render(
    <ModelSelector models={[]} value={null} onChange={noop} loading />,
  );
  expect(screen.getByText('Loading models…')).toBeVisible();

  rerender(<ModelSelector models={[]} value={null} onChange={noop} error />);
  expect(screen.getByText('Models unavailable')).toBeVisible();

  rerender(<ModelSelector models={[]} value={null} onChange={noop} />);
  expect(screen.getByText('No ready models')).toBeVisible();
});
