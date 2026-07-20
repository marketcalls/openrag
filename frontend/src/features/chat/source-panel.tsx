import { FileText } from 'lucide-react';

import type { SourceRef } from '@/api/types';
import { cn } from '@/lib/cn';

export function SourcePanel({
  sources,
  highlightedMarker,
  onSelect,
}: {
  sources: SourceRef[];
  highlightedMarker?: number | null;
  onSelect?: (marker: number) => void;
}) {
  if (!sources.length) return null;
  return (
    <div className="mt-3 flex flex-wrap gap-1.5" aria-label="Sources">
      {sources.map((source) => (
        <button
          key={source.marker}
          type="button"
          onClick={() => onSelect?.(source.marker)}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-md border border-line bg-raised px-2 py-1 text-[12px] text-secondary hover:text-ink',
            highlightedMarker === source.marker && 'border-accent text-ink',
          )}
        >
          <span className="inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-accent-soft px-0.5 text-[10px] font-medium text-accent-on-soft">
            {source.marker}
          </span>
          <FileText className="h-3 w-3 text-muted" aria-hidden />
          <span className="max-w-[220px] truncate">{source.filename}</span>
          {source.version_label ? (
            <span className="text-muted">· {source.version_label}</span>
          ) : null}
          {source.section_label ? (
            <span className="max-w-[180px] truncate text-muted">· {source.section_label}</span>
          ) : null}
          <span className="text-muted">· p. {source.page}</span>
        </button>
      ))}
    </div>
  );
}
