import { Activity, Fingerprint, Gauge, Pencil, Plus, Rocket, Route, ShieldCheck } from 'lucide-react';
import { useState } from 'react';

import type { EmbeddingDeploymentOut, EmbeddingProfileOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { toast } from '@/components/ui/toaster';

import { EmbeddingProfileDialog } from './embedding-profile-dialog';
import {
  useActivateEmbeddingDeployment,
  useEmbeddingDeployments,
  useEmbeddingProfiles,
  usePatchEmbeddingProfile,
  useRequestEmbeddingDeployment,
} from './queries';

function providerLabel(provider: EmbeddingProfileOut['provider_kind']): string {
  if (provider === 'litellm') return 'LiteLLM library';
  if (provider === 'tei') return 'Local TEI';
  return 'Development hash';
}

function deploymentTone(status: EmbeddingDeploymentOut['status']) {
  if (status === 'active' || status === 'ready') return 'success' as const;
  if (status === 'failed') return 'danger' as const;
  if (status === 'building') return 'accent' as const;
  return 'warning' as const;
}

export function EmbeddingProfilesPage() {
  const profiles = useEmbeddingProfiles();
  const deployments = useEmbeddingDeployments();
  const patch = usePatchEmbeddingProfile();
  const deploy = useRequestEmbeddingDeployment();
  const activate = useActivateEmbeddingDeployment();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<EmbeddingProfileOut | null>(null);

  return (
    <>
      <TopBar
        title="Embedding profiles"
        actions={
          <Button variant="primary" size="sm" onClick={() => setAdding(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Register profile
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto bg-[radial-gradient(circle_at_82%_8%,var(--accent-soft),transparent_26%)] p-4">
        <div className="mx-auto max-w-6xl space-y-4">
          <section className="relative overflow-hidden rounded-xl border border-line bg-surface p-5 shadow-sm">
            <div aria-hidden className="absolute inset-y-0 right-0 w-1/3 bg-[repeating-linear-gradient(135deg,transparent,transparent_10px,var(--line-faint)_10px,var(--line-faint)_11px)] opacity-70" />
            <div className="relative max-w-3xl">
              <div className="mb-3 flex h-8 w-8 items-center justify-center rounded-lg bg-accent-soft text-accent">
                <ShieldCheck className="h-4 w-4" aria-hidden />
              </div>
              <h2 className="text-[19px] font-semibold tracking-[-0.02em] text-ink">
                Vectors are a governed data contract
              </h2>
              <p className="mt-1 text-[13px] leading-5 text-secondary">
                Provider, model, dimensions, and limits are immutable after registration. A model change creates a new profile and a new authority generation—never an in-place vector reset.
              </p>
            </div>
          </section>

          <section className="rounded-xl border border-line bg-surface p-4 shadow-sm">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-accent" aria-hidden />
                  <h2 className="text-[14px] font-semibold text-ink">Generation rollout</h2>
                </div>
                <p className="mt-1 text-[12px] text-secondary">
                  Reindex runs beside the live generation. Activation stays locked until every current approved version is verified.
                </p>
              </div>
              {deployments.isFetching ? <Spinner label="Refreshing rollout…" /> : null}
            </div>

            {deployments.isError ? (
              <p role="alert" className="mt-3 rounded-md border border-danger bg-danger-soft p-3 text-[13px] text-danger">
                {deployments.error.message}
              </p>
            ) : null}
            {deployments.data?.length === 0 ? (
              <div className="mt-4 rounded-lg border border-dashed border-line px-4 py-6 text-center text-[12px] text-secondary">
                No governed generation has been deployed yet. Choose an enabled profile below to build one.
              </div>
            ) : null}
            {deployments.data?.length ? (
              <div className="mt-4 space-y-2">
                {deployments.data.map((deployment) => {
                  const profile = profiles.data?.find((candidate) => candidate.id === deployment.profile_id);
                  const finished = deployment.completed_versions + deployment.failed_versions;
                  const percent = deployment.total_versions === 0
                    ? (deployment.scan_complete ? 100 : 0)
                    : Math.min(100, Math.round((finished / deployment.total_versions) * 100));
                  return (
                    <article key={deployment.id} className="rounded-lg border border-line-faint bg-subtle p-3">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-[13px] font-medium text-ink">{profile?.name ?? 'Embedding profile'}</span>
                            <StatusPill tone={deploymentTone(deployment.status)}>{deployment.status}</StatusPill>
                          </div>
                          <p className="mt-1 font-mono text-[10px] text-muted">generation {deployment.generation_id.slice(0, 12)}…</p>
                        </div>
                        {deployment.status === 'ready' ? (
                          <Button
                            variant="primary"
                            size="sm"
                            disabled={activate.isPending}
                            onClick={() => activate.mutate(deployment.id, {
                              onSuccess: () => toast.success('Embedding generation activated'),
                              onError: (error) => toast.error(error.message),
                            })}
                          >
                            <Rocket className="h-3.5 w-3.5" aria-hidden /> Activate
                          </Button>
                        ) : null}
                      </div>
                      <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-line">
                        <div className="h-full rounded-full bg-accent transition-[width]" style={{ width: `${percent}%` }} />
                      </div>
                      <div className="mt-1.5 flex justify-between text-[10px] text-muted">
                        <span>{finished} / {deployment.total_versions} versions</span>
                        <span>{deployment.failure_code ?? (deployment.scan_complete ? 'discovery complete' : 'discovering corpus')}</span>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : null}
          </section>

          {profiles.isPending ? <Spinner label="Loading embedding profiles…" /> : null}
          {profiles.isError ? (
            <p role="alert" className="rounded-md border border-danger bg-danger-soft p-3 text-[13px] text-danger">
              {profiles.error.message}
            </p>
          ) : null}

          {profiles.data?.length === 0 ? (
            <button
              type="button"
              onClick={() => setAdding(true)}
              className="group flex w-full flex-col items-center rounded-xl border border-dashed border-line bg-surface px-5 py-12 text-center transition hover:border-accent hover:bg-accent-soft/30"
            >
              <Route className="mb-3 h-6 w-6 text-muted transition group-hover:text-accent" aria-hidden />
              <span className="text-[14px] font-medium text-ink">No vector contracts registered</span>
              <span className="mt-1 max-w-md text-[12px] text-secondary">
                Register the first LiteLLM or local TEI embedding profile before scheduling a governed reindex.
              </span>
            </button>
          ) : null}

          {profiles.data?.length ? (
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {profiles.data.map((profile) => (
                <article key={profile.id} className="rounded-xl border border-line bg-surface p-4 shadow-sm">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <h3 className="truncate text-[14px] font-semibold text-ink">{profile.name}</h3>
                        <StatusPill tone={profile.enabled ? 'success' : 'warning'}>
                          {profile.enabled ? 'enabled' : 'disabled'}
                        </StatusPill>
                      </div>
                      <p className="mt-1 truncate font-mono text-[11px] text-secondary" title={profile.model_name}>
                        {profile.model_name}
                      </p>
                    </div>
                    <Button variant="ghost" size="icon" aria-label={`Rename ${profile.name}`} onClick={() => setEditing(profile)}>
                      <Pencil className="h-4 w-4" aria-hidden />
                    </Button>
                  </div>

                  <div className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-line-faint bg-line-faint">
                    <div className="bg-subtle px-2 py-2.5">
                      <Gauge className="mb-1 h-3.5 w-3.5 text-muted" aria-hidden />
                      <div className="text-[11px] text-muted">Dimensions</div>
                      <div className="font-mono text-[12px] text-ink">{profile.dimension}</div>
                    </div>
                    <div className="bg-subtle px-2 py-2.5">
                      <Route className="mb-1 h-3.5 w-3.5 text-muted" aria-hidden />
                      <div className="text-[11px] text-muted">Max tokens</div>
                      <div className="font-mono text-[12px] text-ink">{profile.max_input_tokens}</div>
                    </div>
                    <div className="bg-subtle px-2 py-2.5">
                      <Fingerprint className="mb-1 h-3.5 w-3.5 text-muted" aria-hidden />
                      <div className="text-[11px] text-muted">Batch</div>
                      <div className="font-mono text-[12px] text-ink">{profile.batch_size}</div>
                    </div>
                  </div>

                  <div className="mt-3 flex items-center justify-between border-t border-line-faint pt-3">
                    <div>
                      <div className="text-[11px] text-muted">{providerLabel(profile.provider_kind)}</div>
                      <div className="font-mono text-[10px] text-muted" title={profile.config_digest}>
                        {profile.config_digest.slice(0, 12)}…
                      </div>
                    </div>
                    <label className="flex items-center gap-2 text-[11px] text-secondary">
                      Available
                      <input
                        type="checkbox"
                        aria-label={`Enable ${profile.name}`}
                        checked={profile.enabled}
                        disabled={patch.isPending}
                        onChange={(event) =>
                          patch.mutate(
                            { profileId: profile.id, body: { enabled: event.target.checked } },
                            {
                              onSuccess: () => toast.success('Profile availability updated'),
                              onError: (error) => toast.error(error.message),
                            },
                          )
                        }
                        className="h-4 w-4 accent-[var(--accent)]"
                      />
                    </label>
                  </div>
                  <Button
                    className="mt-3 w-full"
                    size="sm"
                    disabled={!profile.enabled || deploy.isPending || deployments.data?.some((item) => item.status === 'building' || item.status === 'ready')}
                    onClick={() => deploy.mutate({ profile_id: profile.id }, {
                      onSuccess: () => toast.success('Safe reindex deployment started'),
                      onError: (error) => toast.error(error.message),
                    })}
                  >
                    <Rocket className="h-3.5 w-3.5" aria-hidden /> Deploy and reindex
                  </Button>
                </article>
              ))}
            </div>
          ) : null}
        </div>
      </div>
      <EmbeddingProfileDialog open={adding} onOpenChange={setAdding} />
      {editing ? (
        <EmbeddingProfileDialog profile={editing} open onOpenChange={(open) => !open && setEditing(null)} />
      ) : null}
    </>
  );
}
