import { useState } from 'react';

import type { RagOperationsFilters, RagOperationsRunOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';

import { useRagRunDetail } from './queries';

function duration(milliseconds: number | null) {
  if (milliseconds === null) return '—';
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(2)}s` : `${milliseconds}ms`;
}

function outcomeTone(outcome: RagOperationsRunOut['outcome']) {
  if (outcome === 'grounded' || outcome === 'conversational') return 'success' as const;
  if (outcome === 'failed') return 'danger' as const;
  if (outcome === 'no_answer') return 'warning' as const;
  return 'accent' as const;
}

function DetailItem({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-line-faint bg-subtle px-3 py-2.5">
      <dt className="text-[10px] font-medium uppercase tracking-[0.1em] text-muted">{label}</dt>
      <dd className="mt-1 break-all font-mono text-[12px] text-ink">{value}</dd>
    </div>
  );
}

export function RunTable({ runs, filters }: { runs: RagOperationsRunOut[]; filters: RagOperationsFilters }) {
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const detail = useRagRunDetail(selectedRunId, filters);

  if (runs.length === 0) {
    return <div className="py-10 text-center text-[12px] text-muted">No completed runs match these filters.</div>;
  }

  return (
    <>
      <Table aria-label="Recent RAG runs">
        <THead>
          <TR><TH>Started</TH><TH>Route</TH><TH>Outcome</TH><TH>Latency</TH><TH>Citations</TH><TH><span className="sr-only">Action</span></TH></TR>
        </THead>
        <TBody>
          {runs.map((run) => (
            <TR key={run.id}>
              <TD className="whitespace-nowrap text-[12px] text-secondary">{new Date(run.accepted_at).toLocaleString()}</TD>
              <TD><span className="font-mono text-[11px]">{run.route}</span></TD>
              <TD><StatusPill tone={outcomeTone(run.outcome)}>{run.outcome.replace('_', ' ')}</StatusPill></TD>
              <TD>{duration(run.latency_ms)}</TD>
              <TD>{run.citation_count}</TD>
              <TD className="text-right">
                <Button variant="ghost" size="sm" aria-label={`Inspect run ${run.run_id}`} onClick={() => setSelectedRunId(run.run_id)}>
                  Inspect
                </Button>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>

      <Dialog open={Boolean(selectedRunId)} onOpenChange={(open) => !open && setSelectedRunId(null)}>
        <DialogContent title="Run trace" description="Safe operational facts only—prompt and response content are never collected." className="max-w-2xl max-sm:left-0 max-sm:top-0 max-sm:h-[100dvh] max-sm:max-w-none max-sm:translate-x-0 max-sm:translate-y-0 max-sm:overflow-y-auto max-sm:rounded-none">
          {detail.isPending ? <Spinner label="Loading run trace…" /> : null}
          {detail.isError ? <p role="alert" className="text-[13px] text-danger">{detail.error.message}</p> : null}
          {detail.data ? (
            <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              <DetailItem label="Run ID" value={detail.data.run_id} />
              <DetailItem label="Trace ID" value={detail.data.trace_id ?? 'unavailable'} />
              <DetailItem label="Release" value={detail.data.release ?? 'unversioned'} />
              <DetailItem label="Total latency" value={duration(detail.data.latency_ms)} />
              <DetailItem label="Time to first token" value={duration(detail.data.ttft_ms ?? null)} />
              <DetailItem label="Retrieval" value={duration(detail.data.retrieval_ms)} />
              <DetailItem label="Provider" value={duration(detail.data.provider_ms)} />
              <DetailItem label="Prompt tokens" value={detail.data.prompt_tokens.toLocaleString()} />
              <DetailItem label="Completion tokens" value={detail.data.completion_tokens.toLocaleString()} />
              <DetailItem label="Evidence retrieved" value={detail.data.retrieval_count} />
              <DetailItem label="Citations" value={detail.data.citation_count} />
              <DetailItem label="Memory items" value={detail.data.memory_item_count} />
            </dl>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
