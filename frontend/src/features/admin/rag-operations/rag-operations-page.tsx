import { Activity, BadgeCheck, CircleDollarSign, Clock3, FlaskConical, RefreshCw, SearchX } from 'lucide-react';
import { useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import type { RagOperationsFilters } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NativeSelect } from '@/components/ui/select';

import { ErrorPanel } from './error-panel';
import { MetricCard } from './metric-card';
import {
  useRagOperationsErrors,
  useRagOperationsOverview,
  useRagOperationsRuns,
  useRagOperationsSeries,
} from './queries';
import { RunTable } from './run-table';
import { ThroughputChart } from './throughput-chart';

type Range = '24h' | '7d' | '30d';

const RANGE_MS: Record<Range, number> = {
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
  '30d': 30 * 24 * 60 * 60 * 1000,
};

function isRange(value: string | null): value is Range {
  return value === '24h' || value === '7d' || value === '30d';
}

function formatDuration(milliseconds: number | null) {
  if (milliseconds === null) return '—';
  return milliseconds >= 1000 ? `${(milliseconds / 1000).toFixed(2)}s` : `${Math.round(milliseconds)}ms`;
}

function percent(value: number) {
  return new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 1 }).format(value);
}

function DashboardSkeleton() {
  return (
    <div aria-label="Loading RAG operations" className="animate-pulse space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((item) => <div key={item} className="h-32 rounded-xl bg-subtle" />)}
      </div>
      <div className="h-80 rounded-xl bg-subtle" />
    </div>
  );
}

export function RagOperationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [windowAnchor, setWindowAnchor] = useState(() => Date.now());
  const rangeParam = searchParams.get('range');
  const range: Range = isRange(rangeParam) ? rangeParam : '24h';
  const route = searchParams.get('route') ?? '';
  const outcome = searchParams.get('outcome') ?? '';
  const orgId = searchParams.get('org_id') ?? '';
  const workspaceId = searchParams.get('workspace_id') ?? '';
  const modelId = searchParams.get('model_id') ?? '';

  const filters = useMemo<RagOperationsFilters>(() => ({
    from: new Date(windowAnchor - RANGE_MS[range]).toISOString(),
    to: new Date(windowAnchor).toISOString(),
    ...(route ? { route: route as RagOperationsFilters['route'] } : {}),
    ...(outcome ? { outcome: outcome as RagOperationsFilters['outcome'] } : {}),
    ...(orgId ? { org_id: orgId } : {}),
    ...(workspaceId ? { workspace_id: workspaceId } : {}),
    ...(modelId ? { model_id: modelId } : {}),
  }), [modelId, orgId, outcome, range, route, windowAnchor, workspaceId]);

  const overview = useRagOperationsOverview(filters);
  const series = useRagOperationsSeries(filters, range === '24h' ? 'hour' : 'day');
  const runs = useRagOperationsRuns(filters);
  const errors = useRagOperationsErrors(filters);
  const queries = [overview, series, runs, errors];
  const isRefreshing = queries.some((query) => query.isFetching);
  const firstError = queries.find((query) => query.isError)?.error;

  function updateFilter(name: 'range' | 'route' | 'outcome', value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(name, value);
    else next.delete(name);
    setSearchParams(next, { replace: true });
    setWindowAnchor(Date.now());
  }

  function applyScope(form: HTMLFormElement) {
    const values = new FormData(form);
    const next = new URLSearchParams(searchParams);
    for (const name of ['org_id', 'workspace_id', 'model_id'] as const) {
      const value = String(values.get(name) ?? '').trim();
      if (value) next.set(name, value);
      else next.delete(name);
    }
    setSearchParams(next, { replace: true });
    setWindowAnchor(Date.now());
  }

  function refreshAll() {
    setWindowAnchor(Date.now());
    void Promise.all(queries.map((query) => query.refetch()));
  }

  return (
    <>
      <TopBar
        title="RAG operations"
        actions={
          <div className="flex items-center gap-2 text-[11px] text-muted">
            <span className="relative flex h-2 w-2" aria-hidden>
              {isRefreshing ? <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-50" /> : null}
              <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
            </span>
            30s live refresh
          </div>
        }
      />
      <main className="flex-1 overflow-y-auto bg-[radial-gradient(circle_at_12%_0%,var(--accent-soft),transparent_22%),linear-gradient(var(--border-faint)_1px,transparent_1px),linear-gradient(90deg,var(--border-faint)_1px,transparent_1px)] bg-[length:auto,28px_28px,28px_28px] p-4">
        <div className="mx-auto max-w-[1500px] space-y-4">
          <section className="overflow-hidden rounded-xl border border-line bg-bg shadow-sm">
            <div className="flex flex-col gap-4 border-b border-line-faint px-5 py-4 lg:flex-row lg:items-end lg:justify-between">
              <div className="max-w-2xl">
                <div className="flex items-center gap-2">
                  <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink text-bg"><Activity className="h-4 w-4" aria-hidden /></span>
                  <div>
                    <h2 className="text-[17px] font-semibold tracking-[-0.025em] text-ink">Production signal, without content exposure</h2>
                    <p className="mt-0.5 text-[11px] text-secondary">Latency, grounding, cost, traces, and grouped failures across the RAG runtime.</p>
                  </div>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-2 sm:flex">
                <Button asChild className="self-end"><Link to="/admin/evaluations"><FlaskConical className="h-3.5 w-3.5" aria-hidden />Evaluations</Link></Button>
                <label className="space-y-1 text-[10px] font-medium uppercase tracking-[0.08em] text-muted">
                  Window
                  <NativeSelect aria-label="Time window" value={range} onChange={(event) => updateFilter('range', event.target.value)} className="min-w-24 normal-case tracking-normal">
                    <option value="24h">Last 24h</option><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option>
                  </NativeSelect>
                </label>
                <label className="space-y-1 text-[10px] font-medium uppercase tracking-[0.08em] text-muted">
                  Route
                  <NativeSelect aria-label="Route" value={route} onChange={(event) => updateFilter('route', event.target.value)} className="min-w-28 normal-case tracking-normal">
                    <option value="">All routes</option><option value="direct">Direct</option><option value="conversation">Conversation</option><option value="rag">RAG</option><option value="analytics">Analytics</option><option value="clarify">Clarify</option>
                  </NativeSelect>
                </label>
                <label className="space-y-1 text-[10px] font-medium uppercase tracking-[0.08em] text-muted">
                  Outcome
                  <NativeSelect aria-label="Outcome" value={outcome} onChange={(event) => updateFilter('outcome', event.target.value)} className="min-w-32 normal-case tracking-normal">
                    <option value="">All outcomes</option><option value="grounded">Grounded</option><option value="conversational">Conversational</option><option value="no_answer">No answer</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option>
                  </NativeSelect>
                </label>
                <Button size="icon" className="self-end" aria-label="Refresh operations data" onClick={refreshAll}><RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} aria-hidden /></Button>
              </div>
            </div>
            <details className="group px-5 py-2.5">
              <summary className="cursor-pointer select-none text-[11px] font-medium text-secondary marker:text-muted">Enterprise scope filters</summary>
              <form
                className="mt-3 grid gap-2 border-t border-line-faint pt-3 sm:grid-cols-3 lg:grid-cols-[1fr_1fr_1fr_auto]"
                onSubmit={(event) => { event.preventDefault(); applyScope(event.currentTarget); }}
              >
                <label className="space-y-1 text-[10px] font-medium uppercase tracking-[0.08em] text-muted">Organization ID<Input name="org_id" aria-label="Organization ID" defaultValue={orgId} placeholder="UUID" pattern="[0-9a-fA-F-]{36}" className="font-mono normal-case tracking-normal" /></label>
                <label className="space-y-1 text-[10px] font-medium uppercase tracking-[0.08em] text-muted">Workspace ID<Input name="workspace_id" aria-label="Workspace ID" defaultValue={workspaceId} placeholder="Requires organization" pattern="[0-9a-fA-F-]{36}" className="font-mono normal-case tracking-normal" /></label>
                <label className="space-y-1 text-[10px] font-medium uppercase tracking-[0.08em] text-muted">Model ID<Input name="model_id" aria-label="Model ID" defaultValue={modelId} placeholder="UUID" pattern="[0-9a-fA-F-]{36}" className="font-mono normal-case tracking-normal" /></label>
                <Button type="submit" size="sm" className="self-end">Apply scope</Button>
              </form>
            </details>
          </section>

          {firstError ? (
            <div role="alert" className="flex items-center justify-between gap-4 rounded-xl border border-danger bg-danger-soft p-3 text-[12px] text-danger">
              <span>{firstError.message}</span><Button size="sm" onClick={refreshAll}>Retry</Button>
            </div>
          ) : null}

          {overview.isPending ? <DashboardSkeleton /> : null}
          {overview.data ? (
            <>
              <section aria-label="RAG performance summary" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <MetricCard label="Queries" value={overview.data.query_count.toLocaleString()} detail={`${overview.data.grounded_count.toLocaleString()} grounded responses`} icon={Activity} />
                <MetricCard label="Grounded rate" value={percent(overview.data.grounded_rate)} detail={`${percent(overview.data.no_answer_rate)} returned a safe no-answer`} icon={BadgeCheck} tone="success" />
                <MetricCard label="P95 latency" value={formatDuration(overview.data.p95_latency_ms ?? null)} detail={`TTFT ${formatDuration(overview.data.average_ttft_ms ?? null)} · target 3–5s`} icon={Clock3} tone={(overview.data.p95_latency_ms ?? 0) > 5000 ? 'danger' : 'neutral'} />
                <MetricCard label="Estimated cost" value={`$${(overview.data.estimated_cost_microusd / 1_000_000).toFixed(2)}`} detail={`${(overview.data.prompt_tokens + overview.data.completion_tokens).toLocaleString()} total tokens`} icon={CircleDollarSign} tone="warning" />
              </section>

              <section className="grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(330px,0.8fr)]">
                <article className="rounded-xl border border-line bg-bg p-4 shadow-sm">
                  <div className="flex items-center justify-between gap-4">
                    <div><h2 className="text-[14px] font-semibold text-ink">Traffic and tail latency</h2><p className="mt-1 text-[11px] text-muted">Query volume <span className="text-accent">bars</span> · p95 latency <span className="text-warning">line</span></p></div>
                    <span className="font-mono text-[10px] text-muted">{range === '24h' ? 'hourly' : 'daily'}</span>
                  </div>
                  <div className="mt-3 border-t border-line-faint pt-2"><ThroughputChart points={series.data ?? []} /></div>
                </article>
                <article className="rounded-xl border border-line bg-bg p-4 shadow-sm">
                  <div><h2 className="text-[14px] font-semibold text-ink">Active error groups</h2><p className="mt-1 text-[11px] text-muted">Deduplicated, redacted, and trace-correlated.</p></div>
                  <div className="mt-3 border-t border-line-faint"><ErrorPanel issues={errors.data?.items ?? []} filters={filters} /></div>
                </article>
              </section>

              <section className="rounded-xl border border-line bg-bg p-4 shadow-sm">
                <div className="mb-3 flex items-end justify-between gap-4">
                  <div><h2 className="text-[14px] font-semibold text-ink">Recent runs</h2><p className="mt-1 text-[11px] text-muted">Content-free execution facts for performance and incident analysis.</p></div>
                  {runs.data?.items.length === 0 ? <SearchX className="h-4 w-4 text-muted" aria-hidden /> : null}
                </div>
                <RunTable runs={runs.data?.items ?? []} filters={filters} />
              </section>
            </>
          ) : null}
        </div>
      </main>
    </>
  );
}
