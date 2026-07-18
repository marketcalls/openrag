import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { CitationProvider } from '@/features/chat/citation-context';

import { Markdown } from './markdown';

function renderMarkdown(content: string, onCitationClick = vi.fn()) {
  render(
    <CitationProvider onCitationClick={onCitationClick}>
      <Markdown content={content} />
    </CitationProvider>,
  );
  return onCitationClick;
}

test('renders GitHub-flavored tables', () => {
  renderMarkdown('| a | b |\n|---|---|\n| 1 | 2 |');
  expect(screen.getByRole('table')).toBeInTheDocument();
});

test('raw HTML from model output is never rendered as elements', () => {
  renderMarkdown(
    'before <img src=x onerror="window.pwned=1"> <script>window.pwned=1</script> after',
  );
  expect(document.querySelector('img')).toBeNull();
  expect(document.querySelector('script')).toBeNull();
  expect((window as { pwned?: number }).pwned).toBeUndefined();
});

test('renders citation markers as interactive citation chips', async () => {
  const onCitationClick = renderMarkdown('Answer text [1] more.');
  await userEvent.setup().click(screen.getByRole('button', { name: 'Citation 1' }));
  expect(onCitationClick).toHaveBeenCalledWith(1);
});

test('fenced code renders with a copy control', () => {
  renderMarkdown('```py\nprint(1)\n```');
  expect(screen.getByRole('button', { name: 'Copy code' })).toBeInTheDocument();
});
