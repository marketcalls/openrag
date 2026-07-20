import { Download } from 'lucide-react';

import type { AnalyticsColumnV1, AnalyticsTableBlockV1 } from '@/api/types';
import { Button } from '@/components/ui/button';

import { downloadText, tableCsv } from './export';
import { SourceMarkers } from './source-markers';

function displayValue(value: string | number | null, format: AnalyticsColumnV1['format']): string {
  if (value === null) return '—';
  if (typeof value === 'string') return value;
  if (format === 'percent') return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value)}%`;
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 4 }).format(value);
}

export function TableBlock({ block }: { block: AnalyticsTableBlockV1 }) {
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-bg shadow-soft">
      <div className="flex flex-wrap items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <h3 className="text-[14px] font-semibold text-ink">{block.title}</h3>
          <SourceMarkers markers={block.source_markers} />
        </div>
        <Button
          size="sm"
          variant="ghost"
          aria-label={`Export ${block.title} as CSV`}
          onClick={() =>
            downloadText(block.title, 'csv', 'text/csv;charset=utf-8', tableCsv(block))
          }
        >
          <Download className="h-3.5 w-3.5" aria-hidden />
          CSV
        </Button>
      </div>
      <div className="max-h-[420px] overflow-auto">
        <table className="w-full min-w-[520px] text-left text-[12px] tabular-nums" aria-label={block.title}>
          <thead className="sticky top-0 z-10 bg-raised text-secondary">
            <tr>
              {block.columns.map((column) => (
                <th key={column.key} scope="col" className="border-b border-line px-4 py-2.5 font-medium">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, index) => (
              <tr key={index} className="even:bg-raised/60">
                {block.columns.map((column) => (
                  <td key={column.key} className="border-b border-line-faint px-4 py-2.5 text-ink">
                    {displayValue(row[column.key] ?? null, column.format)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!block.rows.length ? <p className="px-4 py-6 text-center text-[12px] text-muted">No rows to display.</p> : null}
      </div>
    </section>
  );
}
