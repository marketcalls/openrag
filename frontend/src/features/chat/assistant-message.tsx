import { useState, type ReactNode } from 'react';

import type { SourceRef } from '@/api/types';
import { Markdown } from '@/components/markdown/markdown';

import { CitationProvider } from './citation-context';
import { NoAnswerNotice } from './no-answer-notice';
import { SourcePanel } from './source-panel';

export function AssistantMessage({
  content,
  sources,
  noAnswer = false,
  footer,
}: {
  content: string;
  sources: SourceRef[];
  noAnswer?: boolean;
  footer?: ReactNode;
}) {
  const [highlightedMarker, setHighlightedMarker] = useState<number | null>(null);
  return (
    <div>
      <CitationProvider onCitationClick={setHighlightedMarker}>
        <Markdown content={content} />
      </CitationProvider>
      {noAnswer ? <NoAnswerNotice /> : null}
      {noAnswer && sources.length ? (
        <p className="mb-1 mt-2 text-[12px] text-muted">Nearest sources</p>
      ) : null}
      <SourcePanel
        sources={sources}
        highlightedMarker={highlightedMarker}
        onSelect={setHighlightedMarker}
      />
      {footer}
    </div>
  );
}
