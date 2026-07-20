import { useState, type ReactNode } from 'react';

import type { AnalyticsResponseV1, SourceRef } from '@/api/types';
import { Markdown } from '@/components/markdown/markdown';

import { AnalyticsArtifact } from './analytics/analytics-artifact';
import { CitationProvider } from './citation-context';
import { NoAnswerNotice } from './no-answer-notice';
import { SourcePanel } from './source-panel';

export function AssistantMessage({
  content,
  sources,
  noAnswer = false,
  artifact = null,
  onFollowup,
  followupDisabled = false,
  footer,
}: {
  content: string;
  sources: SourceRef[];
  noAnswer?: boolean;
  artifact?: AnalyticsResponseV1 | null;
  onFollowup?: (question: string) => void;
  followupDisabled?: boolean;
  footer?: ReactNode;
}) {
  const [highlightedMarker, setHighlightedMarker] = useState<number | null>(null);
  return (
    <div>
      <CitationProvider onCitationClick={setHighlightedMarker}>
        <Markdown content={content} />
        {artifact ? (
          <AnalyticsArtifact
            artifact={artifact}
            onFollowup={onFollowup}
            disabled={followupDisabled}
          />
        ) : null}
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
