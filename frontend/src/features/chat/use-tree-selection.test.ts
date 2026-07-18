import { act, renderHook } from '@testing-library/react';

import type { MessageOut } from '@/api/types';

import { useTreeSelection } from './use-tree-selection';

function root(id: string, siblingIndex: number): MessageOut {
  return {
    id,
    parent_message_id: null,
    sibling_index: siblingIndex,
    role: 'user',
    content: id,
    model_id: null,
    prompt_tokens: null,
    completion_tokens: null,
    created_at: `2026-07-18T00:00:0${siblingIndex}Z`,
    citations: [],
    children: [],
  };
}

test('defaults to the newest branch and select navigates to an older branch', () => {
  const messages = [root('u1', 0), root('u1b', 1)];
  const { result, rerender } = renderHook(({ nodes }) => useTreeSelection(nodes), {
    initialProps: { nodes: messages },
  });
  expect(result.current.path[0]?.message.id).toBe('u1b');
  act(() => result.current.select('__root__', 'u1'));
  rerender({ nodes: messages });
  expect(result.current.path[0]?.message.id).toBe('u1');
  expect(result.current.path[0]?.position).toBe(0);
  expect(result.current.path[0]?.siblings).toHaveLength(2);
});
