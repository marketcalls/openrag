import type { RagOperationsSeriesPoint } from '@/api/types';

function bucketLabel(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric',
  }).format(new Date(value));
}

export function ThroughputChart({ points }: { points: RagOperationsSeriesPoint[] }) {
  if (points.length === 0) {
    return <div className="flex h-56 items-center justify-center text-[12px] text-muted">No run data in this window.</div>;
  }

  const width = 760;
  const height = 250;
  const inset = 34;
  const plotWidth = width - inset * 2;
  const plotHeight = height - 58;
  const maxQueries = Math.max(...points.map((point) => point.query_count), 1);
  const maxLatency = Math.max(...points.map((point) => point.p95_latency_ms ?? 0), 1);
  const slot = plotWidth / points.length;
  const firstPoint = points[0];
  if (!firstPoint) return null;
  const latencyCoordinates = points.map((point, index) => {
    const x = inset + slot * index + slot / 2;
    const y = 12 + plotHeight - ((point.p95_latency_ms ?? 0) / maxLatency) * plotHeight;
    return { x, y };
  });
  const latencyPoints = latencyCoordinates.map(({ x, y }) => `${x},${y}`).join(' ');

  return (
    <>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="h-64 w-full"
        role="img"
        aria-label="Query volume and p95 latency over time"
      >
        <defs>
          <linearGradient id="rag-volume" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.82" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0.18" />
          </linearGradient>
        </defs>
        {[0, 0.5, 1].map((ratio) => (
          <line key={ratio} x1={inset} x2={width - inset} y1={12 + plotHeight * ratio} y2={12 + plotHeight * ratio} stroke="var(--border-faint)" />
        ))}
        {points.map((point, index) => {
          const barHeight = (point.query_count / maxQueries) * plotHeight;
          const x = inset + slot * index + slot * 0.23;
          return (
            <rect
              key={point.bucket}
              x={x}
              y={12 + plotHeight - barHeight}
              width={Math.max(3, slot * 0.54)}
              height={barHeight}
              rx="3"
              fill="url(#rag-volume)"
            />
          );
        })}
        <polyline points={latencyPoints} fill="none" stroke="var(--warning)" strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
        {points.map((point, index) => {
          const coordinate = latencyCoordinates[index];
          if (!coordinate) return null;
          return <circle key={`latency-${point.bucket}`} cx={coordinate.x} cy={coordinate.y} r="3" fill="var(--bg)" stroke="var(--warning)" strokeWidth="2" />;
        })}
        <text x={inset} y={height - 11} fill="var(--text-muted)" fontSize="10">{bucketLabel(firstPoint.bucket)}</text>
        <text x={width - inset} y={height - 11} textAnchor="end" fill="var(--text-muted)" fontSize="10">{bucketLabel(points.at(-1)?.bucket ?? firstPoint.bucket)}</text>
      </svg>
      <table className="sr-only" aria-label="Query throughput data">
        <thead><tr><th>Time</th><th>Queries</th><th>Grounded</th><th>No answer</th><th>Failed</th><th>P95 latency</th></tr></thead>
        <tbody>
          {points.map((point) => (
            <tr key={point.bucket}>
              <td>{bucketLabel(point.bucket)}</td><td>{point.query_count}</td><td>{point.grounded_count}</td><td>{point.no_answer_count}</td><td>{point.failed_count}</td><td>{point.p95_latency_ms ?? 'Unavailable'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
