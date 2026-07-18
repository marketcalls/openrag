import { SKIP, visit } from 'unist-util-visit';

interface TextNode {
  type: 'text';
  value: string;
}

interface ParentNode {
  type: string;
  children: Array<Record<string, unknown>>;
}

const CITATION_MARKER = /\[(\d{1,2})\]/g;

/** Turn numeric citation markers in prose into custom citation-chip nodes. */
export function remarkCitations() {
  return (tree: ParentNode): void => {
    visit(
      tree as never,
      'text',
      (node: TextNode, index: number | undefined, parent: ParentNode | undefined) => {
        if (!parent || index === undefined || parent.type === 'link') return;
        CITATION_MARKER.lastIndex = 0;
        if (!CITATION_MARKER.test(node.value)) return;
        CITATION_MARKER.lastIndex = 0;

        const replacement: Array<Record<string, unknown>> = [];
        let cursor = 0;
        let match: RegExpExecArray | null;
        while ((match = CITATION_MARKER.exec(node.value)) !== null) {
          if (match.index > cursor) {
            replacement.push({ type: 'text', value: node.value.slice(cursor, match.index) });
          }
          replacement.push({
            type: 'citationChip',
            data: { hName: 'citation-chip', hProperties: { n: match[1] } },
            children: [],
          });
          cursor = match.index + match[0].length;
        }
        if (cursor < node.value.length) {
          replacement.push({ type: 'text', value: node.value.slice(cursor) });
        }
        parent.children.splice(index, 1, ...replacement);
        return [SKIP, index + replacement.length] as const;
      },
    );
  };
}
