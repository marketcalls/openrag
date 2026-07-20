import { ArrowDownRight, ArrowRight, ArrowUpRight, Download, Sparkles } from 'lucide-react';

import type { AnalyticsKpiV1, AnalyticsResponseV1 } from '@/api/types';
import { Markdown } from '@/components/markdown/markdown';
import { Button } from '@/components/ui/button';

import { ChartBlock } from './chart-block';
import { analyticsJson, downloadText } from './export';
import { SourceMarkers } from './source-markers';
import { TableBlock } from './table-block';

function Trend({ trend }: { trend: AnalyticsKpiV1['trend'] }) {
  if (trend === 'up') return <ArrowUpRight className="h-3.5 w-3.5 text-success" aria-label="Trending up" />;
  if (trend === 'down') return <ArrowDownRight className="h-3.5 w-3.5 text-danger" aria-label="Trending down" />;
  if (trend === 'flat') return <ArrowRight className="h-3.5 w-3.5 text-muted" aria-label="No change" />;
  return null;
}

export function AnalyticsArtifact({
  artifact,
  onFollowup,
  disabled = false,
}: {
  artifact: AnalyticsResponseV1;
  onFollowup?: (question: string) => void;
  disabled?: boolean;
}) {
  return (
    <section className="my-5 overflow-hidden rounded-xl border border-line-strong bg-raised shadow-soft" aria-label={`${artifact.title} analytics`}>
      <header className="border-b border-line bg-bg px-4 py-4 sm:px-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="mb-1 inline-flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-[0.16em] text-accent">
              <Sparkles className="h-3 w-3" aria-hidden />
              Grounded analysis
            </p>
            <h2 className="text-[18px] font-semibold tracking-[-0.02em] text-ink">{artifact.title}</h2>
            {artifact.subtitle ? <p className="mt-1 text-[12px] leading-relaxed text-secondary">{artifact.subtitle}</p> : null}
          </div>
          <Button
            size="sm"
            variant="ghost"
            aria-label={`Export ${artifact.title} as JSON`}
            onClick={() =>
              downloadText(
                artifact.title,
                'json',
                'application/json;charset=utf-8',
                analyticsJson(artifact),
              )
            }
          >
            <Download className="h-3.5 w-3.5" aria-hidden />
            JSON
          </Button>
        </div>
      </header>

      <div className="space-y-3 p-3 sm:p-4">
        {artifact.kpis.length ? (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4" aria-label="Key metrics">
            {artifact.kpis.map((kpi, index) => (
              <article key={`${kpi.label}-${index}`} className="rounded-lg border border-line bg-bg p-3">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-[10px] font-medium uppercase tracking-[0.1em] text-muted">{kpi.label}</p>
                  <Trend trend={kpi.trend} />
                </div>
                <p className="mt-2 text-[21px] font-semibold tracking-[-0.03em] text-ink">{kpi.value}</p>
                {kpi.detail ? <p className="mt-1 text-[11px] leading-relaxed text-secondary">{kpi.detail}</p> : null}
                <div className="mt-2"><SourceMarkers markers={kpi.source_markers} /></div>
              </article>
            ))}
          </div>
        ) : null}

        {artifact.blocks.map((block, index) => {
          if (block.kind === 'bar_chart' || block.kind === 'line_chart') {
            return <ChartBlock key={`${block.kind}-${block.title}-${index}`} block={block} />;
          }
          if (block.kind === 'table') {
            return <TableBlock key={`${block.kind}-${block.title}-${index}`} block={block} />;
          }
          if (block.kind === 'explainer') return (
            <section key={`${block.kind}-${block.title}-${index}`} className="rounded-xl border border-line bg-bg p-4 shadow-soft">
              <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
                <h3 className="text-[14px] font-semibold text-ink">{block.title}</h3>
                <SourceMarkers markers={block.source_markers} />
              </div>
              <Markdown content={block.body_markdown} allowLinks={false} />
            </section>
          );
          return null;
        })}

        {artifact.suggested_followups.length ? (
          <section className="rounded-xl border border-line bg-bg px-4 py-3" aria-label="Suggested follow-ups">
            <p className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">Explore next</p>
            <div className="mt-2 flex flex-wrap gap-2">
              {artifact.suggested_followups.map((question) => (
                <Button
                  key={question}
                  size="sm"
                  disabled={disabled || !onFollowup}
                  aria-label={`Ask: ${question}`}
                  onClick={() => onFollowup?.(question)}
                  className="h-auto min-h-7 whitespace-normal py-1.5 text-left"
                >
                  {question}
                  <ArrowUpRight className="h-3 w-3 shrink-0" aria-hidden />
                </Button>
              ))}
            </div>
          </section>
        ) : null}
      </div>
    </section>
  );
}
