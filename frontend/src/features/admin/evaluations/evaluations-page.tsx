import {
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  Beaker,
  CircleDollarSign,
  FlaskConical,
  LockKeyhole,
  Play,
  Plus,
} from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent } from 'react';

import type { EvaluationRunOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill, type StatusTone } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';
import { useAdminModels } from '@/features/admin/models/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';

import {
  useCreateEvaluationDataset,
  useCreateEvaluationRun,
  useCreateEvaluationVersion,
  useEvaluationDatasets,
  useEvaluationRun,
  useEvaluationRuns,
  useEvaluationVersions,
} from './queries';

const METRICS = [
  ['recall', 'Retrieval recall'],
  ['precision', 'Retrieval precision'],
  ['mrr', 'MRR'],
  ['ndcg', 'nDCG'],
  ['citation_precision', 'Citation precision'],
  ['citation_recall', 'Citation recall'],
  ['groundedness', 'Groundedness'],
  ['answer_relevance', 'Answer relevance'],
  ['correct_refusal', 'Correct refusal'],
] as const;

type DraftCase = {
  question: string;
  shouldRefuse: boolean;
  documentVersionId: string;
  evidenceSpanId: string;
};

const emptyCase = (): DraftCase => ({
  question: '', shouldRefuse: false, documentVersionId: '', evidenceSpanId: '',
});

function percent(value: number | null) {
  return value === null ? '—' : new Intl.NumberFormat(undefined, {
    style: 'percent', maximumFractionDigits: 1,
  }).format(value);
}

function shortId(id: string) {
  return id.slice(0, 8);
}

function statusTone(status: EvaluationRunOut['status']): StatusTone {
  if (status === 'completed') return 'success';
  if (status === 'failed' || status === 'cancelled') return 'danger';
  if (status === 'running') return 'accent';
  return 'warning';
}

function DatasetDialog({
  open, onOpenChange, workspaceId, onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  workspaceId: string;
  onCreated: (id: string) => void;
}) {
  const mutation = useCreateEvaluationDataset();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate({ workspace_id: workspaceId, name: name.trim(), description: description.trim() }, {
      onSuccess: (dataset) => {
        toast.success('Evaluation dataset created');
        onCreated(dataset.id);
        onOpenChange(false);
      },
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Create evaluation dataset" description="Group immutable golden-question versions for one workspace.">
        <form className="space-y-4" onSubmit={submit}>
          <div><Label htmlFor="dataset-name">Name</Label><Input id="dataset-name" required maxLength={120} value={name} onChange={(event) => setName(event.target.value)} /></div>
          <div><Label htmlFor="dataset-description">Description</Label><Input id="dataset-description" maxLength={500} value={description} onChange={(event) => setDescription(event.target.value)} /></div>
          {mutation.isError ? <p role="alert" className="text-[12px] text-danger">{mutation.error.message}</p> : null}
          <DialogFooter><Button onClick={() => onOpenChange(false)}>Cancel</Button><Button type="submit" variant="primary" disabled={mutation.isPending}>{mutation.isPending ? 'Creating…' : 'Create dataset'}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function VersionDialog({ open, onOpenChange, datasetId }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  datasetId: string | null;
}) {
  const mutation = useCreateEvaluationVersion(datasetId);
  const [label, setLabel] = useState('');
  const [cases, setCases] = useState<DraftCase[]>([emptyCase()]);

  function patchCase(index: number, patch: Partial<DraftCase>) {
    setCases((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    mutation.mutate({
      label: label.trim() || null,
      cases: cases.map((item) => ({
        question: item.question.trim(),
        should_refuse: item.shouldRefuse,
        expected_evidence: item.shouldRefuse ? [] : [{
          document_version_id: item.documentVersionId,
          evidence_span_id: item.evidenceSpanId,
        }],
      })),
    }, {
      onSuccess: () => {
        toast.success('Immutable dataset version sealed');
        onOpenChange(false);
      },
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto" title="Seal a golden dataset version" description="Evidence must reference approved workspace content. Once sealed, cases cannot be changed or deleted.">
        <form className="space-y-4" onSubmit={submit}>
          <div className="rounded-lg border border-warning bg-warning-soft p-3 text-[12px] text-warning"><LockKeyhole className="mr-2 inline h-4 w-4" aria-hidden />This creates an immutable, content-digested version for reproducible comparisons.</div>
          <div><Label htmlFor="version-label">Version label</Label><Input id="version-label" maxLength={120} placeholder="Approved policy baseline" value={label} onChange={(event) => setLabel(event.target.value)} /></div>
          <div className="space-y-3">
            {cases.map((item, index) => (
              <fieldset key={index} className="rounded-lg border border-line p-3">
                <legend className="px-1 text-[12px] font-semibold text-ink">Case {index + 1}</legend>
                <div><Label htmlFor={`case-question-${index}`}>Question</Label><Input id={`case-question-${index}`} required value={item.question} onChange={(event) => patchCase(index, { question: event.target.value })} /></div>
                <label className="mt-3 flex items-center gap-2 text-[12px] text-secondary"><input type="checkbox" checked={item.shouldRefuse} onChange={(event) => patchCase(index, { shouldRefuse: event.target.checked })} />Expected safe refusal</label>
                {!item.shouldRefuse ? <div className="mt-3 grid gap-3 sm:grid-cols-2">
                  <div><Label htmlFor={`document-version-${index}`}>Document version ID</Label><Input id={`document-version-${index}`} required pattern="[0-9a-fA-F-]{36}" value={item.documentVersionId} onChange={(event) => patchCase(index, { documentVersionId: event.target.value })} /></div>
                  <div><Label htmlFor={`evidence-span-${index}`}>Evidence span ID</Label><Input id={`evidence-span-${index}`} required pattern="[0-9a-fA-F-]{36}" value={item.evidenceSpanId} onChange={(event) => patchCase(index, { evidenceSpanId: event.target.value })} /></div>
                </div> : null}
              </fieldset>
            ))}
          </div>
          <Button size="sm" onClick={() => setCases((current) => [...current, emptyCase()])}><Plus className="h-3.5 w-3.5" aria-hidden />Add case</Button>
          {mutation.isError ? <p role="alert" className="text-[12px] text-danger">{mutation.error.message}</p> : null}
          <DialogFooter><Button onClick={() => onOpenChange(false)}>Cancel</Button><Button type="submit" variant="primary" disabled={mutation.isPending}>{mutation.isPending ? 'Sealing…' : 'Seal version'}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function RunDialog({ open, onOpenChange, versionId, caseCount, onQueued }: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  versionId: string | null;
  caseCount: number;
  onQueued: (run: EvaluationRunOut) => void;
}) {
  const models = useAdminModels();
  const mutation = useCreateEvaluationRun();
  const enabledModels = useMemo(
    () => (models.data ?? []).filter(
      (model) => model.enabled && model.supports_chat_completion,
    ),
    [models.data],
  );
  const evaluatorModels = useMemo(
    () => enabledModels.filter(
      (model) => model.supports_structured_json && model.supports_verifier,
    ),
    [enabledModels],
  );
  const [modelId, setModelId] = useState('');
  const [maxCases, setMaxCases] = useState(Math.max(caseCount, 1));
  const [maxTokens, setMaxTokens] = useState(50_000);
  const [maxCostUsd, setMaxCostUsd] = useState(5);
  const [confirmed, setConfirmed] = useState(false);
  const [useLlmJudge, setUseLlmJudge] = useState(false);
  const [evaluatorModelId, setEvaluatorModelId] = useState('');

  useEffect(() => {
    if (!open) return;
    setMaxCases(Math.max(caseCount, 1));
    setModelId((current) => current || enabledModels[0]?.id || '');
    setEvaluatorModelId((current) => current || evaluatorModels[0]?.id || '');
    setUseLlmJudge(false);
    setConfirmed(false);
  }, [caseCount, enabledModels, evaluatorModels, open]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!versionId || !confirmed) return;
    mutation.mutate({
      dataset_version_id: versionId,
      model_id: modelId,
      evaluator_model_id: useLlmJudge ? evaluatorModelId : null,
      use_llm_judge: useLlmJudge,
      max_cases: maxCases,
      max_tokens: maxTokens,
      max_cost_microusd: Math.round(maxCostUsd * 1_000_000),
      client_request_id: crypto.randomUUID(),
    }, {
      onSuccess: (run) => { onQueued(run); onOpenChange(false); },
    });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Run evaluation" description="Set hard provider budgets before work enters the isolated evaluation queue.">
        <form className="space-y-4" onSubmit={submit}>
          <div><Label htmlFor="evaluation-model">Model</Label><NativeSelect id="evaluation-model" required value={modelId} onChange={(event) => setModelId(event.target.value)}><option value="">Select a model</option>{enabledModels.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</NativeSelect></div>
          <label className="flex items-center gap-2 text-[12px] font-medium text-ink">
            <input
              type="checkbox"
              aria-label="Use LLM judge"
              checked={useLlmJudge}
              disabled={!evaluatorModels.length}
              onChange={(event) => setUseLlmJudge(event.target.checked)}
            />
            Use a structured-output verifier for answer relevance
          </label>
          {useLlmJudge ? <div><Label htmlFor="evaluator-model">Evaluator model</Label><NativeSelect id="evaluator-model" required value={evaluatorModelId} onChange={(event) => setEvaluatorModelId(event.target.value)}><option value="">Select an evaluator</option>{evaluatorModels.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}</NativeSelect></div> : null}
          <div className="grid grid-cols-3 gap-3">
            <div><Label htmlFor="maximum-cases">Maximum cases</Label><Input id="maximum-cases" type="number" min={1} max={caseCount || 1} value={maxCases} onChange={(event) => setMaxCases(event.target.valueAsNumber)} /></div>
            <div><Label htmlFor="maximum-tokens">Maximum evaluation tokens</Label><Input id="maximum-tokens" type="number" min={1} value={maxTokens} onChange={(event) => setMaxTokens(event.target.valueAsNumber)} /></div>
            <div><Label htmlFor="maximum-cost">Maximum cost (USD)</Label><Input id="maximum-cost" type="number" min={0.01} step={0.01} value={maxCostUsd} onChange={(event) => setMaxCostUsd(event.target.valueAsNumber)} /></div>
          </div>
          <div className="rounded-lg border border-line bg-raised p-3 text-[12px] text-secondary"><CircleDollarSign className="mr-2 inline h-4 w-4 text-warning" aria-hidden />The worker stops scheduling cases once either the token or cost ceiling is reached.</div>
          <label className="flex items-start gap-2 text-[12px] font-medium text-ink"><input aria-label="I confirm this evaluation budget" className="mt-0.5" type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />I confirm this evaluation budget and understand it may incur provider charges.</label>
          {mutation.isError ? <p role="alert" className="text-[12px] text-danger">{mutation.error.message}</p> : null}
          <DialogFooter><Button onClick={() => onOpenChange(false)}>Cancel</Button><Button type="submit" variant="primary" disabled={!confirmed || !modelId || (useLlmJudge && !evaluatorModelId) || mutation.isPending}>{mutation.isPending ? 'Queuing…' : 'Queue evaluation'}</Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function MetricComparison({ runs }: { runs: EvaluationRunOut[] }) {
  const completed = runs.filter((run) => run.status === 'completed');
  const [baselineId, setBaselineId] = useState('');
  const [candidateId, setCandidateId] = useState('');
  const baseline = completed.find((run) => run.id === baselineId) ?? completed[1] ?? completed[0];
  const candidate = completed.find((run) => run.id === candidateId) ?? completed[0];

  return (
    <section className="rounded-xl border border-line bg-bg p-4 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div><h2 className="text-[14px] font-semibold text-ink">Regression comparison</h2><p className="mt-1 text-[11px] text-muted">Compare only runs over the exact same sealed corpus.</p></div>
        <div className="grid grid-cols-2 gap-2">
          <label className="text-[10px] uppercase tracking-[0.08em] text-muted">Baseline<NativeSelect aria-label="Baseline run" value={baseline?.id ?? ''} onChange={(event) => setBaselineId(event.target.value)} className="mt-1 normal-case tracking-normal">{completed.map((run) => <option key={run.id} value={run.id}>{shortId(run.id)} · {new Date(run.created_at).toLocaleDateString()}</option>)}</NativeSelect></label>
          <label className="text-[10px] uppercase tracking-[0.08em] text-muted">Candidate<NativeSelect aria-label="Candidate run" value={candidate?.id ?? ''} onChange={(event) => setCandidateId(event.target.value)} className="mt-1 normal-case tracking-normal">{completed.map((run) => <option key={run.id} value={run.id}>{shortId(run.id)} · {new Date(run.created_at).toLocaleDateString()}</option>)}</NativeSelect></label>
        </div>
      </div>
      <div className="mt-3">
        <Table aria-label="Evaluation metric comparison">
          <THead><TR><TH>Metric</TH><TH>Baseline</TH><TH>Candidate</TH><TH>Change</TH></TR></THead>
          <TBody>{METRICS.map(([key, label]) => {
            const before = baseline?.[key] ?? null;
            const after = candidate?.[key] ?? null;
            const delta = before === null || after === null ? null : after - before;
            const DeltaIcon = delta === null || Math.abs(delta) < 0.0001 ? ArrowRight : delta > 0 ? ArrowUpRight : ArrowDownRight;
            const deltaLabel = delta === null ? 'Unavailable' : Math.abs(delta) < 0.0001 ? 'No change' : delta > 0 ? 'Improved' : 'Regressed';
            return <TR key={key}><TD className="font-medium">{label}</TD><TD>{percent(before)}</TD><TD>{percent(after)}</TD><TD><span className={`inline-flex items-center gap-1 ${delta !== null && delta < 0 ? 'text-danger' : delta !== null && delta > 0 ? 'text-success' : 'text-secondary'}`}><DeltaIcon className="h-3.5 w-3.5" aria-hidden />{deltaLabel}{delta === null ? '' : ` ${percent(Math.abs(delta))}`}</span></TD></TR>;
          })}</TBody>
        </Table>
      </div>
    </section>
  );
}

function RunDetailDialog({ runId, onOpenChange }: { runId: string | null; onOpenChange: (open: boolean) => void }) {
  const run = useEvaluationRun(runId);
  return <Dialog open={Boolean(runId)} onOpenChange={onOpenChange}><DialogContent className="max-w-3xl" title="Evaluation case results" description="Only identifiers, numeric scores, safe error codes, and answer digests are retained.">
    {run.isPending ? <Spinner label="Loading evaluation results…" /> : null}
    {run.isError ? <p role="alert" className="text-danger">{run.error.message}</p> : null}
    {run.data ? <Table aria-label="Evaluation case failures"><THead><TR><TH>Case</TH><TH>Status</TH><TH>Recall</TH><TH>Grounded</TH><TH>Latency</TH><TH>Error</TH></TR></THead><TBody>{run.data.results.filter((result) => result.status === 'failed' || result.error_code || (result.groundedness ?? 1) < 0.8).map((result) => <TR key={result.id}><TD>#{result.sequence + 1}</TD><TD>{result.status}</TD><TD>{percent(result.recall)}</TD><TD>{percent(result.groundedness)}</TD><TD>{result.latency_ms}ms</TD><TD className="font-mono text-[11px]">{result.error_code ?? 'quality threshold'}</TD></TR>)}</TBody></Table> : null}
    <DialogFooter><Button onClick={() => onOpenChange(false)}>Close</Button></DialogFooter>
  </DialogContent></Dialog>;
}

export function EvaluationsPage() {
  const { workspaceId } = useWorkspace();
  const datasets = useEvaluationDatasets(workspaceId);
  const [datasetId, setDatasetId] = useState('');
  const versions = useEvaluationVersions(datasetId || null);
  const [versionId, setVersionId] = useState('');
  const runs = useEvaluationRuns(versionId || null);
  const [datasetOpen, setDatasetOpen] = useState(false);
  const [versionOpen, setVersionOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [detailRunId, setDetailRunId] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');

  useEffect(() => {
    if (!datasetId && datasets.data?.[0]) setDatasetId(datasets.data[0].id);
  }, [datasetId, datasets.data]);
  useEffect(() => {
    if (!versionId && versions.data?.[0]) setVersionId(versions.data[0].id);
  }, [versionId, versions.data]);

  const selectedVersion = versions.data?.find((version) => version.id === versionId) ?? versions.data?.[0];
  const activeRuns = useMemo(() => (runs.data ?? []).filter((run) => run.status === 'queued' || run.status === 'running'), [runs.data]);
  const firstError = [datasets, versions, runs].find((query) => query.isError)?.error;

  return (
    <>
      <TopBar title="RAG evaluations" actions={<span className="text-[11px] text-muted">Immutable corpora · isolated workers</span>} />
      <main className="flex-1 overflow-y-auto bg-[radial-gradient(circle_at_85%_0%,var(--accent-soft),transparent_24%),linear-gradient(var(--border-faint)_1px,transparent_1px),linear-gradient(90deg,var(--border-faint)_1px,transparent_1px)] bg-[length:auto,28px_28px,28px_28px] p-4">
        <div className="mx-auto max-w-[1500px] space-y-4">
          <section className="rounded-xl border border-line bg-bg p-5 shadow-sm">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div className="max-w-2xl"><div className="flex items-center gap-2"><span className="flex h-9 w-9 items-center justify-center rounded-lg bg-ink text-bg"><FlaskConical className="h-4 w-4" aria-hidden /></span><div><h2 className="text-[17px] font-semibold tracking-[-0.025em] text-ink">Quality gates before production</h2><p className="mt-0.5 text-[11px] text-secondary">Measure retrieval, citations, grounding, refusal, latency, tokens, and cost on versioned evidence.</p></div></div></div>
              <div className="flex flex-wrap gap-2"><Button onClick={() => setDatasetOpen(true)}><Plus className="h-3.5 w-3.5" aria-hidden />Dataset</Button><Button disabled={!datasetId} onClick={() => setVersionOpen(true)}><LockKeyhole className="h-3.5 w-3.5" aria-hidden />Seal version</Button><Button variant="primary" disabled={!selectedVersion} onClick={() => setRunOpen(true)}><Play className="h-3.5 w-3.5" aria-hidden />Run evaluation</Button></div>
            </div>
            <div className="mt-4 grid gap-3 border-t border-line-faint pt-4 sm:grid-cols-2">
              <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted">Dataset<NativeSelect aria-label="Evaluation dataset" className="mt-1 normal-case tracking-normal" value={datasetId} onChange={(event) => { setDatasetId(event.target.value); setVersionId(''); }}><option value="">Select a dataset</option>{datasets.data?.map((dataset) => <option key={dataset.id} value={dataset.id}>{dataset.name}</option>)}</NativeSelect></label>
              <label className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted">Sealed corpus<NativeSelect aria-label="Dataset version" className="mt-1 normal-case tracking-normal" value={versionId} onChange={(event) => setVersionId(event.target.value)}><option value="">Select a version</option>{versions.data?.map((version) => <option key={version.id} value={version.id}>v{version.version} · {version.label ?? 'Unlabelled'} · {version.case_count} cases</option>)}</NativeSelect></label>
            </div>
          </section>

          <div aria-live="polite" className={announcement ? 'rounded-xl border border-success bg-success-soft p-3 text-[12px] text-success' : 'sr-only'}>{announcement}</div>
          {firstError ? <p role="alert" className="rounded-xl border border-danger bg-danger-soft p-3 text-[12px] text-danger">{firstError.message}</p> : null}
          {activeRuns.length ? <section aria-label="Active evaluations" className="grid gap-3 sm:grid-cols-2">{activeRuns.map((run) => <article key={run.id} className="rounded-xl border border-accent bg-bg p-4 shadow-sm"><div className="flex items-center justify-between"><span className="font-mono text-[11px] text-secondary">{shortId(run.id)}</span><StatusPill tone={statusTone(run.status)}>{run.status}</StatusPill></div><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-subtle"><div className="h-full bg-accent transition-[width]" style={{ width: `${run.total_cases ? (run.completed_cases / run.total_cases) * 100 : 2}%` }} /></div><p className="mt-2 text-[11px] text-muted">{run.completed_cases}/{run.total_cases} cases · {run.consumed_tokens.toLocaleString()}/{run.max_tokens.toLocaleString()} tokens</p></article>)}</section> : null}

          {runs.isPending && versionId ? <Spinner label="Loading evaluation runs…" /> : null}
          {runs.data ? <MetricComparison runs={runs.data} /> : null}

          <section className="rounded-xl border border-line bg-bg p-4 shadow-sm">
            <div className="mb-3"><h2 className="text-[14px] font-semibold text-ink">Evaluation history</h2><p className="mt-1 text-[11px] text-muted">Run progress is polled only while work is active and this tab is visible.</p></div>
            <Table aria-label="Evaluation runs"><THead><TR><TH>Run</TH><TH>Status</TH><TH>Progress</TH><TH>Grounded</TH><TH>Tokens</TH><TH>Cost</TH><TH><span className="sr-only">Actions</span></TH></TR></THead><TBody>{(runs.data ?? []).map((run) => <TR key={run.id}><TD className="font-mono text-[11px]">{shortId(run.id)}</TD><TD><StatusPill tone={statusTone(run.status)}>{run.status}</StatusPill></TD><TD>{run.completed_cases}/{run.total_cases}</TD><TD>{percent(run.groundedness)}</TD><TD>{run.consumed_tokens.toLocaleString()}</TD><TD>${(run.consumed_cost_microusd / 1_000_000).toFixed(3)}</TD><TD><Button size="sm" aria-label={`Inspect evaluation ${run.id}`} onClick={() => setDetailRunId(run.id)}>Inspect</Button></TD></TR>)}</TBody></Table>
            {!versionId ? <div className="py-8 text-center text-[12px] text-muted"><Beaker className="mx-auto mb-2 h-5 w-5" aria-hidden />Select a sealed dataset version to inspect its runs.</div> : null}
          </section>
        </div>
      </main>
      {workspaceId ? <DatasetDialog open={datasetOpen} onOpenChange={setDatasetOpen} workspaceId={workspaceId} onCreated={setDatasetId} /> : null}
      <VersionDialog open={versionOpen} onOpenChange={setVersionOpen} datasetId={datasetId || null} />
      <RunDialog open={runOpen} onOpenChange={setRunOpen} versionId={selectedVersion?.id ?? null} caseCount={selectedVersion?.case_count ?? 0} onQueued={() => { setAnnouncement('Evaluation queued'); toast.success('Evaluation queued'); }} />
      <RunDetailDialog runId={detailRunId} onOpenChange={(open) => { if (!open) setDetailRunId(null); }} />
    </>
  );
}
