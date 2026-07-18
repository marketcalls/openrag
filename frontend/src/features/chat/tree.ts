import type { MessageOut } from '@/api/types';

export const ROOT = '__root__';

export interface PathEntry {
  message: MessageOut;
  siblings: string[];
  position: number;
}

export type SelectionOverrides = Readonly<Record<string, string>>;

export function branchKeyOf(message: MessageOut): string {
  return message.parent_message_id ?? ROOT;
}

function compareSiblings(first: MessageOut, second: MessageOut): number {
  return (
    first.sibling_index - second.sibling_index ||
    first.created_at.localeCompare(second.created_at) ||
    first.id.localeCompare(second.id)
  );
}

function collectMessages(nodes: readonly MessageOut[]): MessageOut[] {
  const collected: MessageOut[] = [];
  const visited = new Set<string>();

  const visit = (node: MessageOut) => {
    if (visited.has(node.id)) return;
    visited.add(node.id);
    collected.push(node);
    for (const child of node.children) visit(child);
  };

  for (const node of nodes) visit(node);
  return collected;
}

/**
 * Select one renderable path through a recursive backend message tree. The
 * collector also accepts legacy flat input, while orphan and cycle guards keep
 * corrupt data from escaping the root-owned conversation.
 */
export function selectActivePath(
  messages: readonly MessageOut[],
  overrides: SelectionOverrides,
): PathEntry[] {
  const byBranch = new Map<string, MessageOut[]>();
  for (const message of collectMessages(messages)) {
    const branch = branchKeyOf(message);
    const siblings = byBranch.get(branch);
    if (siblings) siblings.push(message);
    else byBranch.set(branch, [message]);
  }
  for (const siblings of byBranch.values()) siblings.sort(compareSiblings);

  const path: PathEntry[] = [];
  const visited = new Set<string>();
  let branch = ROOT;
  for (;;) {
    const siblings = byBranch.get(branch);
    if (!siblings?.length) break;
    const override = overrides[branch];
    const chosen = siblings.find((message) => message.id === override) ?? siblings.at(-1)!;
    if (visited.has(chosen.id)) break;
    visited.add(chosen.id);
    path.push({
      message: chosen,
      siblings: siblings.map((message) => message.id),
      position: siblings.indexOf(chosen),
    });
    branch = chosen.id;
  }
  return path;
}
