import { CitationChip } from '../citation-chip';

export function SourceMarkers({ markers }: { markers: number[] }) {
  return (
    <div className="flex flex-wrap items-center gap-0.5" aria-label="Artifact sources">
      <span className="mr-1 text-[10px] font-medium uppercase tracking-[0.12em] text-muted">
        Sources
      </span>
      {markers.map((marker) => (
        <CitationChip key={marker} n={marker} />
      ))}
    </div>
  );
}
