import { BadgeCheck, CircleAlert, Gauge, ScanSearch } from 'lucide-react';

import type { AnswerQualityOverview } from '@/api/types';

function percent(value: number) {
  return new Intl.NumberFormat(undefined, {
    style: 'percent',
    maximumFractionDigits: 1,
  }).format(value);
}

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  const resolved = value ?? 0;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-secondary">{label}</span>
        <span className="font-mono font-medium text-ink">{value === null ? '—' : percent(value)}</span>
      </div>
      <div
        aria-label={`${label} score`}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={Math.round(resolved * 100)}
        className="h-1.5 overflow-hidden rounded-full bg-subtle"
        role="progressbar"
      >
        <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${resolved * 100}%` }} />
      </div>
    </div>
  );
}

export function QualityPanel({ quality }: { quality: AnswerQualityOverview }) {
  const healthy = quality.pass_rate >= 0.95 && quality.worker_failed_count === 0;
  const totalExceptions = quality.skipped_count + quality.worker_failed_count;

  return (
    <article className="relative overflow-hidden rounded-xl border border-line bg-bg shadow-sm">
      <div className="absolute inset-y-0 left-0 w-1 bg-accent" aria-hidden />
      <div className="grid gap-5 p-5 lg:grid-cols-[minmax(230px,0.65fr)_minmax(320px,1fr)_minmax(360px,1.2fr)] lg:items-center">
        <div>
          <div className="flex items-center gap-2">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent-soft text-accent"><Gauge className="h-4 w-4" aria-hidden /></span>
            <div>
              <h2 className="text-[14px] font-semibold text-ink">Grounded answer quality</h2>
              <p className="mt-0.5 text-[10px] uppercase tracking-[0.1em] text-muted">Asynchronous release audit</p>
            </div>
          </div>
          <div className="mt-4 flex items-end gap-2">
            <span className="text-[34px] font-semibold tracking-[-0.05em] text-ink">{percent(quality.pass_rate)}</span>
            <span className={`mb-1.5 rounded-full px-2 py-0.5 text-[10px] font-medium ${healthy ? 'bg-success-soft text-success' : 'bg-warning-soft text-warning'}`}>
              {healthy ? 'target met' : 'review needed'}
            </span>
          </div>
          <p className="mt-1 text-[11px] text-secondary">{quality.passed_count.toLocaleString()} of {quality.completed_count.toLocaleString()} completed audits passed.</p>
        </div>

        <div className="space-y-4 border-y border-line-faint py-4 lg:border-x lg:border-y-0 lg:px-5 lg:py-1">
          <ScoreBar label="Grounding confidence" value={quality.average_grounding_score ?? null} />
          <ScoreBar label="Answer completeness" value={quality.average_completeness_score ?? null} />
          <div className="flex items-center justify-between text-[10px] text-muted">
            <span>Audit coverage</span><span className="font-mono">{percent(quality.completion_rate)}</span>
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line-faint bg-line-faint sm:grid-cols-4">
          <div className="bg-bg p-3"><dt className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-muted"><BadgeCheck className="h-3 w-3 text-success" aria-hidden />Validated</dt><dd className="mt-2 text-[18px] font-semibold tabular-nums text-ink">{quality.passed_count.toLocaleString()}</dd></div>
          <div className="bg-bg p-3"><dt className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-muted"><CircleAlert className="h-3 w-3 text-danger" aria-hidden />Rejected</dt><dd className="mt-2 text-[18px] font-semibold tabular-nums text-ink">{quality.rejected_count.toLocaleString()}</dd></div>
          <div className="bg-bg p-3"><dt className="flex items-center gap-1.5 text-[10px] uppercase tracking-[0.08em] text-muted"><ScanSearch className="h-3 w-3 text-accent" aria-hidden />Pending</dt><dd className="mt-2 text-[18px] font-semibold tabular-nums text-ink">{quality.pending_count.toLocaleString()}</dd></div>
          <div className="bg-bg p-3"><dt className="text-[10px] uppercase tracking-[0.08em] text-muted">Exceptions</dt><dd className="mt-2 text-[18px] font-semibold tabular-nums text-ink">{totalExceptions.toLocaleString()}</dd><p className="mt-0.5 text-[9px] text-muted">{quality.worker_failed_count} worker failed</p></div>
        </dl>
      </div>
    </article>
  );
}
