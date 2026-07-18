import { remark } from 'remark';

import { remarkCitations } from './remark-citations';

interface Node {
  type: string;
  value?: string;
  children?: Node[];
  data?: { hName?: string; hProperties?: { n?: string } };
}

function transform(markdown: string): Node {
  const processor = remark().use(remarkCitations);
  return processor.runSync(processor.parse(markdown)) as unknown as Node;
}

function flatten(node: Node, result: Node[] = []): Node[] {
  result.push(node);
  for (const child of node.children ?? []) flatten(child, result);
  return result;
}

test('splits citation markers into nodes while preserving surrounding prose', () => {
  const nodes = flatten(transform('Revenue rose 12% [1] and churn fell [2].'));
  const citations = nodes.filter((node) => node.data?.hName === 'citation-chip');
  expect(citations.map((node) => node.data?.hProperties?.n)).toEqual(['1', '2']);
  expect(nodes.filter((node) => node.type === 'text').map((node) => node.value)).toEqual([
    'Revenue rose 12% ',
    ' and churn fell ',
    '.',
  ]);
});

test('leaves ordinary bracketed prose untouched', () => {
  const nodes = flatten(transform('No citations here [not one].'));
  expect(nodes.some((node) => node.data?.hName === 'citation-chip')).toBe(false);
});

test('does not rewrite markers inside inline code', () => {
  const nodes = flatten(transform('Use `arr[1]` to index.'));
  expect(nodes.some((node) => node.data?.hName === 'citation-chip')).toBe(false);
});
