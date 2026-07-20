import { CircleAlert, Clock3, Gauge, ScanSearch, Sparkles, Zap } from 'lucide-react';

import type { EnrichmentOperationsOverview } from '@/api/types';

function percent(value: number) {
  return new Intl.NumberFormat(undefined, {
    style: 'percent',
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(value);
}

function age(seconds: number | null) {
  if (seconds === null) return 'No backlog';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${(seconds / 3600).toFixed(1)}h`;
}

export function EnrichmentPanel({ enrichment }: { enrichment: EnrichmentOperationsOverview }) {
  const oldestAge = enrichment.oldest_pending_age_seconds ?? null;
  const healthy = enrichment.failed_count === 0 && (oldestAge === null || oldestAge <= 300);
  const tokens = enrichment.prompt_tokens + enrichment.completion_tokens;

  return (
    <article className="relative overflow-hidden rounded-xl border border-line bg-bg shadow-sm">
      <div className={`absolute inset-y-0 left-0 w-1 ${healthy ? 'bg-success' : 'bg-warning'}`} aria-hidden />
      <div className="grid gap-5 p-5 lg:grid-cols-[minmax(240px,0.7fr)_minmax(320px,1fr)_minmax(430px,1.35fr)] lg:items-center">
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-success-soft text-success"><Sparkles className="h-4 w-4" aria-hidden /></span>
            <div>
              <h2 className="text-[14px] font-semibold text-ink">Document enrichment</h2>
              <p className="mt-0.5 text-[10px] uppercase tracking-[0.1em] text-muted">Content-free asynchronous health</p>
            </div>
          </div>
          <div className="mt-4 flex items-end gap-2">
            <span className="text-[34px] font-semibold tracking-[-0.05em] text-ink">{percent(enrichment.completion_rate)}</span>
            <span className={`mb-1.5 rounded-full px-2 py-0.5 text-[10px] font-medium ${healthy ? 'bg-success-soft text-success' : 'bg-warning-soft text-warning'}`}>
              {healthy ? 'queue healthy' : 'attention needed'}
            </span>
          </div>
          <p className="mt-1 text-[11px] text-secondary">{enrichment.completed_count.toLocaleString()} of {enrichment.scheduled_count.toLocaleString()} jobs completed in this window.</p>
        </div>

        <div className="space-y-3 border-y border-line-faint py-4 lg:border-x lg:border-y-0 lg:px-5 lg:py-1">
          <div className="flex items-center justify-between text-[11px]"><span className="flex items-center gap-1.5 text-secondary"><Gauge className="h-3.5 w-3.5 text-accent" aria-hidden />Evidence accepted</span><strong className="font-mono text-ink">{percent(enrichment.evidence_success_rate)}</strong></div>
          <div className="h-1.5 overflow-hidden rounded-full bg-subtle" role="progressbar" aria-label="Enrichment evidence success rate" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(enrichment.evidence_success_rate * 100)}><div className="h-full rounded-full bg-success transition-[width]" style={{ width: `${enrichment.evidence_success_rate * 100}%` }} /></div>
          <div className="flex items-center justify-between text-[11px]"><span className="flex items-center gap-1.5 text-secondary"><Clock3 className="h-3.5 w-3.5 text-warning" aria-hidden />Oldest current job</span><strong className="font-mono text-ink">{age(oldestAge)}</strong></div>
          <p className="text-[10px] text-muted">Current backlog is all-time for the selected scope; throughput and tokens follow the selected window.</p>
        </div>

        <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line-faint bg-line-faint sm:grid-cols-4">
          <div className="bg-bg p-3"><dt className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-muted"><Zap className="h-3 w-3 text-success" aria-hidden />Generated</dt><dd className="mt-2 text-[18px] font-semibold tabular-nums text-ink">{enrichment.generated_evidence.toLocaleString()}</dd></div>
          <div className="bg-bg p-3"><dt className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-muted"><ScanSearch className="h-3 w-3 text-accent" aria-hidden />Backlog</dt><dd className="mt-2 text-[18px] font-semibold tabular-nums text-ink">{enrichment.pending_count.toLocaleString()}</dd><p className="mt-0.5 text-[9px] text-muted">{enrichment.pending_count.toLocaleString()} pending</p></div>
          <div className="bg-bg p-3"><dt className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-muted"><CircleAlert className="h-3 w-3 text-danger" aria-hidden />Exceptions</dt><dd className="mt-2 text-[18px] font-semibold tabular-nums text-ink">{(enrichment.failed_count + enrichment.skipped_count).toLocaleString()}</dd><p className="mt-0.5 text-[9px] text-muted">{enrichment.failed_count} failed</p></div>
          <div className="bg-bg p-3"><dt className="text-[10px] uppercase tracking-[0.08em] text-muted">Model tokens</dt><dd className="mt-2 text-[18px] font-semibold tabular-nums text-ink">{tokens.toLocaleString()}</dd><p className="mt-0.5 text-[9px] text-muted">{enrichment.invalid_evidence} invalid outputs</p></div>
        </dl>
      </div>
    </article>
  );
}
