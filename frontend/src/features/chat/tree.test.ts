import type { MessageOut } from '@/api/types';

import {
  ROOT,
  activeLeafId,
  branchKeyOf,
  selectActivePath,
  treeContainsMessage,
} from './tree';

function message(overrides: Partial<MessageOut> & { id: string }): MessageOut {
  return {
    parent_message_id: null,
    sibling_index: 0,
    role: 'user',
    content: `content-${overrides.id}`,
    model_id: null,
    prompt_tokens: null,
    completion_tokens: null,
    created_at: '2026-07-18T00:00:00Z',
    citations: [],
    children: [],
    ...overrides,
  };
}

function linearTree(): MessageOut[] {
  const assistant2 = message({ id: 'a2', role: 'assistant', parent_message_id: 'u2' });
  const user2 = message({ id: 'u2', parent_message_id: 'a1', children: [assistant2] });
  const assistant1 = message({ id: 'a1', role: 'assistant', parent_message_id: 'u1', children: [user2] });
  return [message({ id: 'u1', children: [assistant1] })];
}

test('a recursive linear thread returns its complete active path', () => {
  const path = selectActivePath(linearTree(), {});
  expect(path.map((entry) => entry.message.id)).toEqual(['u1', 'a1', 'u2', 'a2']);
  expect(path.every((entry) => entry.siblings.length === 1 && entry.position === 0)).toBe(true);
});

test('the newest edited user sibling and its descendants win by default', () => {
  const tree = linearTree();
  const assistant1 = tree[0]!.children[0]!;
  assistant1.children.push(
    message({
      id: 'u2b',
      parent_message_id: 'a1',
      sibling_index: 1,
      children: [message({ id: 'a2b', role: 'assistant', parent_message_id: 'u2b' })],
    }),
  );

  const path = selectActivePath(tree, {});

  expect(path.map((entry) => entry.message.id)).toEqual(['u1', 'a1', 'u2b', 'a2b']);
  expect(path[2]!.siblings).toEqual(['u2', 'u2b']);
  expect(path[2]!.position).toBe(1);
});

test('an override navigates to an older sibling and its own descendants', () => {
  const tree = linearTree();
  tree[0]!.children[0]!.children.push(
    message({
      id: 'u2b',
      parent_message_id: 'a1',
      sibling_index: 1,
      children: [message({ id: 'a2b', role: 'assistant', parent_message_id: 'u2b' })],
    }),
  );

  const path = selectActivePath(tree, { a1: 'u2' });

  expect(path.map((entry) => entry.message.id)).toEqual(['u1', 'a1', 'u2', 'a2']);
  expect(path[2]!.position).toBe(0);
});

test('a regenerated assistant is a navigable sibling branch', () => {
  const tree = linearTree();
  const user2 = tree[0]!.children[0]!.children[0]!;
  user2.children.push(
    message({ id: 'a2b', role: 'assistant', parent_message_id: 'u2', sibling_index: 1 }),
  );

  expect(selectActivePath(tree, {}).at(-1)!.message.id).toBe('a2b');
  expect(selectActivePath(tree, { u2: 'a2' }).at(-1)!.message.id).toBe('a2');
});

test('an invalid override falls back to the newest sibling', () => {
  expect(selectActivePath(linearTree(), { [ROOT]: 'missing' })[0]!.message.id).toBe('u1');
});

test('siblings are ordered by sibling index, timestamp, then id', () => {
  const roots = [
    message({ id: 'b', created_at: '2026-07-18T00:00:02Z' }),
    message({ id: 'a', created_at: '2026-07-18T00:00:01Z' }),
  ];
  expect(selectActivePath(roots, {})[0]!.siblings).toEqual(['a', 'b']);
});

test('empty, orphaned, and cyclic data are safe', () => {
  expect(selectActivePath([], {})).toEqual([]);
  expect(selectActivePath([message({ id: 'x', parent_message_id: 'ghost' })], {})).toEqual([]);
  const cyclic = message({ id: 'cycle' });
  cyclic.children.push(cyclic);
  expect(selectActivePath([cyclic], {}).map((entry) => entry.message.id)).toEqual(['cycle']);
});

test('branchKeyOf uses the root sentinel for top-level messages', () => {
  expect(branchKeyOf(message({ id: 'u1' }))).toBe(ROOT);
  expect(branchKeyOf(message({ id: 'a1', parent_message_id: 'u1' }))).toBe('u1');
});

test('recursive helpers find persisted stream results and the selected continuation leaf', () => {
  const tree = linearTree();
  const path = selectActivePath(tree, {});
  expect(treeContainsMessage(tree, 'a2')).toBe(true);
  expect(treeContainsMessage(tree, 'missing')).toBe(false);
  expect(activeLeafId(path)).toBe('a2');
  expect(activeLeafId([])).toBeNull();
});
