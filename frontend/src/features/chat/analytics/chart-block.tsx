import type { AnalyticsChartBlockV1 } from '@/api/types';

import { SourceMarkers } from './source-markers';

const WIDTH = 680;
const HEIGHT = 300;
const LEFT = 58;
const RIGHT = 18;
const TOP = 24;
const BOTTOM = 58;
const PLOT_WIDTH = WIDTH - LEFT - RIGHT;
const PLOT_HEIGHT = HEIGHT - TOP - BOTTOM;
const COLORS = ['#4f46e5', '#0891b2', '#d97706', '#059669', '#be185d', '#7c3aed', '#475569', '#dc2626'];

function domain(block: AnalyticsChartBlockV1): [number, number] {
  const values = block.series.flatMap((series) => series.values).filter((value): value is number => value !== null);
  if (!values.length) return [0, 1];
  let minimum = Math.min(...values);
  let maximum = Math.max(...values);
  if (block.kind === 'bar_chart') {
    minimum = Math.min(0, minimum);
    maximum = Math.max(0, maximum);
  }
  if (minimum === maximum) {
    const padding = Math.abs(minimum) * 0.1 || 1;
    minimum -= padding;
    maximum += padding;
  }
  const padding = (maximum - minimum) * 0.08;
  return [minimum - padding, maximum + padding];
}

function valueLabel(value: number): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2, notation: 'compact' }).format(value);
}

function lineSegments(values: (number | null)[], point: (value: number, index: number) => string): string[] {
  const segments: string[] = [];
  let current: string[] = [];
  values.forEach((value, index) => {
    if (value === null) {
      if (current.length) segments.push(current.join(' '));
      current = [];
      return;
    }
    current.push(`${current.length ? 'L' : 'M'} ${point(value, index)}`);
  });
  if (current.length) segments.push(current.join(' '));
  return segments;
}

export function ChartBlock({ block }: { block: AnalyticsChartBlockV1 }) {
  const [minimum, maximum] = domain(block);
  const y = (value: number) => TOP + ((maximum - value) / (maximum - minimum)) * PLOT_HEIGHT;
  const categoryWidth = PLOT_WIDTH / block.categories.length;
  const zeroY = y(0);
  const ticks = Array.from({ length: 5 }, (_, index) => maximum - ((maximum - minimum) * index) / 4);

  return (
    <section className="rounded-xl border border-line bg-bg p-4 shadow-soft">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-[14px] font-semibold text-ink">{block.title}</h3>
          <p className="mt-0.5 text-[11px] text-muted">{block.y_label} by {block.x_label}</p>
        </div>
        <SourceMarkers markers={block.source_markers} />
      </div>
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
          role="img"
          aria-label={block.title}
          className="min-w-[560px] text-muted"
        >
          <title>{block.title}</title>
          <desc>{`${block.kind === 'bar_chart' ? 'Bar' : 'Line'} chart of ${block.y_label} by ${block.x_label}. The data is also available in the following table.`}</desc>
          {ticks.map((tick) => {
            const tickY = y(tick);
            return (
              <g key={tick}>
                <line x1={LEFT} x2={WIDTH - RIGHT} y1={tickY} y2={tickY} stroke="currentColor" strokeOpacity="0.16" />
                <text x={LEFT - 8} y={tickY + 4} textAnchor="end" fontSize="10" fill="currentColor">
                  {valueLabel(tick)}
                </text>
              </g>
            );
          })}
          {block.categories.map((category, index) => (
            <text
              key={category}
              x={LEFT + categoryWidth * (index + 0.5)}
              y={HEIGHT - 30}
              textAnchor="middle"
              fontSize="10"
              fill="currentColor"
            >
              {category.length > 14 ? `${category.slice(0, 13)}…` : category}
              <title>{category}</title>
            </text>
          ))}
          <text x={LEFT + PLOT_WIDTH / 2} y={HEIGHT - 5} textAnchor="middle" fontSize="11" fill="currentColor">
            {block.x_label}
          </text>
          <text
            transform={`translate(13 ${TOP + PLOT_HEIGHT / 2}) rotate(-90)`}
            textAnchor="middle"
            fontSize="11"
            fill="currentColor"
          >
            {block.y_label}
          </text>
          {block.kind === 'bar_chart'
            ? block.series.flatMap((series, seriesIndex) => {
                const groupWidth = categoryWidth * 0.72;
                const barWidth = Math.max(2, groupWidth / block.series.length);
                return series.values.flatMap((value, categoryIndex) => {
                  if (value === null) return [];
                  const valueY = y(value);
                  return [
                    <rect
                      key={`${series.name}-${block.categories[categoryIndex]}`}
                      x={LEFT + categoryWidth * categoryIndex + (categoryWidth - groupWidth) / 2 + seriesIndex * barWidth}
                      y={Math.min(valueY, zeroY)}
                      width={Math.max(1, barWidth - 3)}
                      height={Math.max(1, Math.abs(zeroY - valueY))}
                      rx="2"
                      fill={COLORS[seriesIndex % COLORS.length]}
                    >
                      <title>{`${block.categories[categoryIndex]} — ${series.name}: ${value}`}</title>
                    </rect>,
                  ];
                });
              })
            : block.series.flatMap((series, seriesIndex) => {
                const x = (index: number) => LEFT + categoryWidth * (index + 0.5);
                const color = COLORS[seriesIndex % COLORS.length];
                return [
                  ...lineSegments(series.values, (value, index) => `${x(index)} ${y(value)}`).map((path, pathIndex) => (
                    <path key={`${series.name}-path-${pathIndex}`} d={path} fill="none" stroke={color} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                  )),
                  ...series.values.flatMap((value, index) => value === null ? [] : [
                    <circle key={`${series.name}-${index}`} cx={x(index)} cy={y(value)} r="3.5" fill={color}>
                      <title>{`${block.categories[index]} — ${series.name}: ${value}`}</title>
                    </circle>,
                  ]),
                ];
              })}
        </svg>
      </div>
      {block.series.length > 1 ? (
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1" aria-label="Chart legend">
          {block.series.map((series, index) => (
            <span key={series.name} className="inline-flex items-center gap-1.5 text-[11px] text-secondary">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: COLORS[index % COLORS.length] }} aria-hidden />
              {series.name}
            </span>
          ))}
        </div>
      ) : null}
      <table className="sr-only" aria-label={`${block.title} data`}>
        <caption>{`${block.title}: ${block.y_label} by ${block.x_label}`}</caption>
        <thead>
          <tr>
            <th scope="col">{block.x_label}</th>
            {block.series.map((series) => <th key={series.name} scope="col">{series.name}</th>)}
          </tr>
        </thead>
        <tbody>
          {block.categories.map((category, index) => (
            <tr key={category}>
              <th scope="row">{category}</th>
              {block.series.map((series) => <td key={series.name}>{series.values[index] ?? 'No data'}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
