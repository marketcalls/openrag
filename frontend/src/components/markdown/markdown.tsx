import { type ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { CitationChip } from '@/features/chat/citation-chip';
import { remarkCitations } from '@/features/chat/remark-citations';

import { CodeBlock } from './code-block';

const components = {
  'citation-chip': CitationChip,
  pre: CodeBlock,
  code: ({ children, className }: { children?: ReactNode; className?: string }) =>
    className ? (
      <code className={className}>{children}</code>
    ) : (
      <code className="rounded-sm bg-subtle px-1 py-0.5 font-mono text-[13px]">{children}</code>
    ),
  a: ({ href, children }: { href?: string; children?: ReactNode }) => (
    <a href={href} target="_blank" rel="noreferrer noopener" className="text-accent underline">
      {children}
    </a>
  ),
  p: ({ children }: { children?: ReactNode }) => <p className="my-2 leading-relaxed">{children}</p>,
  ul: ({ children }: { children?: ReactNode }) => (
    <ul className="my-2 list-disc pl-5">{children}</ul>
  ),
  ol: ({ children }: { children?: ReactNode }) => (
    <ol className="my-2 list-decimal pl-5">{children}</ol>
  ),
  h1: ({ children }: { children?: ReactNode }) => (
    <h2 className="mb-2 mt-4 text-[16px] font-semibold">{children}</h2>
  ),
  h2: ({ children }: { children?: ReactNode }) => (
    <h3 className="mb-2 mt-4 text-[15px] font-semibold">{children}</h3>
  ),
  h3: ({ children }: { children?: ReactNode }) => (
    <h4 className="mb-1 mt-3 text-[14px] font-semibold">{children}</h4>
  ),
  table: ({ children }: { children?: ReactNode }) => (
    <div className="my-2 overflow-x-auto rounded-md border border-line">
      <table className="w-full text-[13px] tabular-nums">{children}</table>
    </div>
  ),
  thead: ({ children }: { children?: ReactNode }) => (
    <thead className="sticky top-0 bg-raised text-left">{children}</thead>
  ),
  th: ({ children }: { children?: ReactNode }) => (
    <th className="border-b border-line px-2.5 py-1.5 font-medium text-secondary">{children}</th>
  ),
  td: ({ children }: { children?: ReactNode }) => (
    <td className="border-b border-line-faint px-2.5 py-1.5">{children}</td>
  ),
  tr: ({ children }: { children?: ReactNode }) => <tr className="even:bg-raised">{children}</tr>,
  blockquote: ({ children }: { children?: ReactNode }) => (
    <blockquote className="my-2 border-l-2 border-line-strong pl-3 text-secondary">
      {children}
    </blockquote>
  ),
} as Components;

export function Markdown({ content }: { content: string }) {
  return (
    <div className="text-[15px] leading-[1.6] text-ink">
      <ReactMarkdown
        skipHtml
        remarkPlugins={[remarkGfm, remarkCitations]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
