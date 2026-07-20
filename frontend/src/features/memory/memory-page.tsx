import {
  BrainCircuit,
  Download,
  Fingerprint,
  History,
  Pencil,
  Plus,
  ShieldCheck,
  Trash2,
} from 'lucide-react';
import { useState } from 'react';

import type { MemoryOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { toast } from '@/components/ui/toaster';
import { useWorkspace } from '@/features/workspaces/workspace-context';

import { MemoryDialog } from './memory-dialog';
import {
  exportMemories,
  useForgetMemory,
  useMemories,
  useMemoryPreferences,
  usePatchMemoryPreferences,
} from './queries';

function sourceLabel(memory: MemoryOut): string {
  const latest = memory.provenance?.at(-1)?.source_kind;
  if (latest === 'explicit_user_action') return 'Explicit user action';
  if (latest === 'verified_event') return 'Verified event';
  if (latest === 'approved_procedure') return 'Approved procedure';
  return 'User message';
}

export function MemoryPage() {
  const { workspaceId } = useWorkspace();
  const [includeHistory, setIncludeHistory] = useState(false);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<MemoryOut | null>(null);
  const memories = useMemories(workspaceId, includeHistory);
  const preferences = useMemoryPreferences(workspaceId);
  const patchPreferences = usePatchMemoryPreferences(workspaceId);
  const forget = useForgetMemory(workspaceId);

  return (
    <>
      <TopBar
        title="Memory"
        actions={
          <>
            <Button
              size="sm"
              disabled={!workspaceId}
              onClick={() => {
                if (!workspaceId) return;
                void exportMemories(workspaceId)
                  .then(() => toast.success('Memory export created'))
                  .catch((error: unknown) =>
                    toast.error(error instanceof Error ? error.message : 'Export failed'),
                  );
              }}
            >
              <Download className="h-3.5 w-3.5" aria-hidden /> Export
            </Button>
            <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
              <Plus className="h-3.5 w-3.5" aria-hidden /> Add memory
            </Button>
          </>
        }
      />
      <div className="flex-1 overflow-y-auto bg-[radial-gradient(circle_at_15%_0%,var(--accent-soft),transparent_24%)] p-4">
        <div className="mx-auto max-w-5xl space-y-4">
          <section className="relative overflow-hidden rounded-xl border border-line bg-surface p-5 shadow-sm">
            <div
              aria-hidden
              className="absolute -right-12 -top-16 h-44 w-44 rounded-full border-[28px] border-accent-soft"
            />
            <div className="relative max-w-2xl">
              <div className="mb-3 flex h-8 w-8 items-center justify-center rounded-lg bg-accent-soft text-accent">
                <BrainCircuit className="h-4 w-4" aria-hidden />
              </div>
              <h2 className="text-[19px] font-semibold tracking-[-0.02em] text-ink">
                Memory you can inspect, correct, and forget
              </h2>
              <p className="mt-1 text-[13px] leading-5 text-secondary">
                OpenRAG keeps durable memory separate from chat history. Every active item is
                provenance-bound, workspace-scoped, and removable without exposing internal
                suppression fingerprints.
              </p>
            </div>
          </section>

          <section className="grid gap-3 md:grid-cols-[1fr_1.35fr]">
            <div className="rounded-xl border border-line bg-surface p-4 shadow-sm">
              <div className="flex items-center gap-2">
                <ShieldCheck className="h-4 w-4 text-success" aria-hidden />
                <h2 className="text-[14px] font-semibold text-ink">Learning permission</h2>
              </div>
              <p className="mt-1 text-[12px] leading-5 text-secondary">
                Automatic extraction is off by default. Enabling permission never activates shared
                or procedural memory.
              </p>
              {preferences.isPending ? <Spinner label="Loading memory preferences…" /> : null}
              {preferences.data ? (
                <label className="mt-4 flex items-center justify-between rounded-lg border border-line-faint bg-subtle p-3">
                  <span>
                    <span className="block text-[12px] font-medium text-ink">
                      Automatic extraction permission
                    </span>
                    <span className="block text-[10px] text-muted">
                      Private workspace memory only
                    </span>
                  </span>
                  <input
                    type="checkbox"
                    aria-label="Automatic memory extraction"
                    checked={preferences.data.extraction_enabled}
                    disabled={patchPreferences.isPending}
                    onChange={(event) =>
                      patchPreferences.mutate(
                        { extraction_enabled: event.target.checked },
                        {
                          onSuccess: () => toast.success('Memory preference updated'),
                          onError: (error) => toast.error(error.message),
                        },
                      )
                    }
                    className="h-4 w-4 accent-[var(--accent)]"
                  />
                </label>
              ) : null}
            </div>

            <div className="rounded-xl border border-line bg-surface p-4 shadow-sm">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="flex items-center gap-2">
                    <Fingerprint className="h-4 w-4 text-accent" aria-hidden />
                    <h2 className="text-[14px] font-semibold text-ink">Governance contract</h2>
                  </div>
                  <p className="mt-1 text-[12px] text-secondary">
                    The initial policy admits explicit semantic and episodic memory only.
                  </p>
                </div>
                <StatusPill tone="success">private</StatusPill>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-line-faint bg-line-faint text-center">
                <div className="bg-subtle px-2 py-3">
                  <div className="font-mono text-[14px] text-ink">100</div>
                  <div className="text-[10px] text-muted">page limit</div>
                </div>
                <div className="bg-subtle px-2 py-3">
                  <div className="font-mono text-[14px] text-ink">4 KB</div>
                  <div className="text-[10px] text-muted">text bound</div>
                </div>
                <div className="bg-subtle px-2 py-3">
                  <div className="font-mono text-[14px] text-ink">8 KB</div>
                  <div className="text-[10px] text-muted">JSON bound</div>
                </div>
              </div>
            </div>
          </section>

          <section className="rounded-xl border border-line bg-surface p-4 shadow-sm">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-[14px] font-semibold text-ink">Saved memory</h2>
                <p className="mt-0.5 text-[11px] text-muted">
                  Only active items will be eligible for prompt selection.
                </p>
              </div>
              <label className="flex items-center gap-2 text-[11px] text-secondary">
                <History className="h-3.5 w-3.5" aria-hidden /> Show forgotten
                <input
                  type="checkbox"
                  checked={includeHistory}
                  onChange={(event) => setIncludeHistory(event.target.checked)}
                  className="h-4 w-4 accent-[var(--accent)]"
                />
              </label>
            </div>

            {memories.isPending ? (
              <div className="py-8">
                <Spinner label="Loading memory…" />
              </div>
            ) : null}
            {memories.isError ? (
              <p
                role="alert"
                className="mt-4 rounded-md bg-danger-soft p-3 text-[12px] text-danger"
              >
                {memories.error.message}
              </p>
            ) : null}
            {memories.data?.items.length === 0 ? (
              <button
                type="button"
                onClick={() => setAdding(true)}
                className="mt-4 w-full rounded-lg border border-dashed border-line px-4 py-8 text-center transition hover:border-accent hover:bg-accent-soft/30"
              >
                <BrainCircuit className="mx-auto h-5 w-5 text-muted" aria-hidden />
                <span className="mt-2 block text-[13px] font-medium text-ink">
                  No saved memory yet
                </span>
                <span className="mt-1 block text-[11px] text-secondary">
                  Add a stable preference or fact you want OpenRAG to reuse.
                </span>
              </button>
            ) : null}
            {memories.data?.items.length ? (
              <div className="mt-4 grid gap-3 md:grid-cols-2">
                {memories.data.items.map((memory) => (
                  <article
                    key={memory.id}
                    className="group rounded-lg border border-line-faint bg-subtle p-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <code className="text-[11px] text-accent">{memory.canonical_key}</code>
                          <StatusPill tone={memory.status === 'active' ? 'success' : 'warning'}>
                            {memory.status}
                          </StatusPill>
                        </div>
                        <p className="mt-2 text-[13px] leading-5 text-ink">{memory.content}</p>
                      </div>
                      <div className="flex opacity-70 transition group-hover:opacity-100 group-focus-within:opacity-100">
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Edit ${memory.canonical_key}`}
                          disabled={memory.status !== 'active'}
                          onClick={() => setEditing(memory)}
                        >
                          <Pencil className="h-3.5 w-3.5" aria-hidden />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Forget ${memory.canonical_key}`}
                          disabled={memory.status !== 'active' || forget.isPending}
                          onClick={() => {
                            if (
                              !window.confirm(
                                `Forget ${memory.canonical_key}? OpenRAG will suppress relearning the same value.`,
                              )
                            )
                              return;
                            forget.mutate(memory.id, {
                              onSuccess: () => toast.success('Memory forgotten'),
                              onError: (error) => toast.error(error.message),
                            });
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5" aria-hidden />
                        </Button>
                      </div>
                    </div>
                    <div className="mt-3 flex items-center justify-between border-t border-line-faint pt-2 text-[10px] text-muted">
                      <span>{sourceLabel(memory)}</span>
                      <span>
                        {memory.memory_type} · importance {Math.round(memory.importance * 100)}%
                      </span>
                    </div>
                  </article>
                ))}
              </div>
            ) : null}
          </section>
        </div>
      </div>
      <MemoryDialog workspaceId={workspaceId} open={adding} onOpenChange={setAdding} />
      {editing ? (
        <MemoryDialog
          workspaceId={workspaceId}
          memory={editing}
          open
          onOpenChange={(open) => !open && setEditing(null)}
        />
      ) : null}
    </>
  );
}
