import { AlertTriangle, BellRing, ChevronRight } from 'lucide-react';
import { useState } from 'react';

import type { ErrorIssueOut } from '@/api/types';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';

import { useRagErrorDetail } from './queries';

export function ErrorPanel({ issues }: { issues: ErrorIssueOut[] }) {
  const [selectedIssueId, setSelectedIssueId] = useState<string | null>(null);
  const detail = useRagErrorDetail(selectedIssueId);

  if (issues.length === 0) {
    return (
      <div className="flex min-h-64 flex-col items-center justify-center text-center">
        <span className="flex h-9 w-9 items-center justify-center rounded-full bg-success-soft text-success"><BellRing className="h-4 w-4" aria-hidden /></span>
        <p className="mt-3 text-[13px] font-medium text-ink">No active errors in this window</p>
        <p className="mt-1 text-[11px] text-muted">The grouped error stream is quiet.</p>
      </div>
    );
  }

  return (
    <>
      <div className="divide-y divide-line-faint">
        {issues.map((issue) => (
          <button
            key={issue.id}
            type="button"
            aria-label={`Inspect error ${issue.code}`}
            onClick={() => setSelectedIssueId(issue.id)}
            className="group flex w-full items-center gap-3 px-1 py-3 text-left hover:bg-subtle"
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-danger-soft text-danger"><AlertTriangle className="h-4 w-4" aria-hidden /></span>
            <span className="min-w-0 flex-1">
              <span className="flex items-center gap-2">
                <span className="truncate font-mono text-[11px] font-medium text-ink">{issue.code}</span>
                {issue.alert_state === 'firing' ? <StatusPill tone="danger">firing</StatusPill> : null}
              </span>
              <span className="mt-1 block truncate text-[11px] text-secondary">{issue.service} · {issue.environment} · {issue.occurrence_count.toLocaleString()} occurrences</span>
              <span className="mt-0.5 block text-[10px] text-muted">Last seen {new Date(issue.last_seen_at).toLocaleString()}</span>
            </span>
            <ChevronRight className="h-4 w-4 text-muted transition-transform group-hover:translate-x-0.5" aria-hidden />
          </button>
        ))}
      </div>

      <Dialog open={Boolean(selectedIssueId)} onOpenChange={(open) => !open && setSelectedIssueId(null)}>
        <DialogContent title="Error group" description="Redacted diagnostic metadata and correlated occurrences." className="max-w-2xl max-sm:left-0 max-sm:top-0 max-sm:h-[100dvh] max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0 max-sm:overflow-y-auto max-sm:rounded-none">
          {detail.isPending ? <Spinner label="Loading error group…" /> : null}
          {detail.isError ? <p role="alert" className="text-[13px] text-danger">{detail.error.message}</p> : null}
          {detail.data ? (
            <div className="space-y-4">
              <div className="rounded-lg border border-line bg-subtle p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill tone="danger">{detail.data.issue.category}</StatusPill>
                  <span className="font-mono text-[12px] font-medium text-ink">{detail.data.issue.code}</span>
                </div>
                <p className="mt-2 font-mono text-[12px] text-secondary">{detail.data.issue.exception_type}</p>
                <p className="mt-1 break-all font-mono text-[10px] text-muted">{detail.data.issue.top_frame ?? 'No application frame recorded'}</p>
              </div>
              <div>
                <h3 className="text-[12px] font-semibold text-ink">Latest occurrences</h3>
                <div className="mt-2 max-h-64 space-y-2 overflow-y-auto">
                  {detail.data.occurrences.map((occurrence) => (
                    <article key={occurrence.id} className="rounded-lg border border-line-faint p-3">
                      <div className="flex justify-between gap-3 text-[11px]">
                        <span className="font-mono text-ink">{occurrence.http_method ?? 'JOB'} {occurrence.route_template ?? detail.data.issue.service}</span>
                        <span className="text-muted">{occurrence.http_status ?? '—'}</span>
                      </div>
                      <div className="mt-2 grid gap-1 font-mono text-[10px] text-muted sm:grid-cols-2">
                        <span>trace {occurrence.trace_id ?? 'unavailable'}</span>
                        <span>{new Date(occurrence.occurred_at).toLocaleString()}</span>
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
