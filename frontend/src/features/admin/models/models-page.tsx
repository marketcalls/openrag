import { Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { ModelOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { ModelFormDialog } from './model-form-dialog';
import { useAdminModels, useDeleteModel, usePatchModel, useProbeModel } from './queries';

function providerLabel(provider: ModelOut['provider_kind']): string {
  if (provider === 'openai_compatible') return 'OpenAI-compatible';
  if (provider === 'openai') return 'OpenAI';
  return 'Ollama';
}

function contextLabel(value: number | null): string {
  if (value === null) return '—';
  if (value >= 1000) return `${Math.round(value / 1000)}k`;
  return value.toLocaleString();
}

export function ModelsPage() {
  const models = useAdminModels();
  const patchModel = usePatchModel();
  const deleteModel = useDeleteModel();
  const probeModel = useProbeModel();
  const [addOpen, setAddOpen] = useState(false);
  const [editing, setEditing] = useState<ModelOut | null>(null);
  const [removing, setRemoving] = useState<ModelOut | null>(null);

  return (
    <>
      <TopBar
        title="Models"
        actions={
          <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Add model
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-6xl">
          {models.isPending ? <Spinner label="Loading models…" /> : null}
          {models.isError ? (
            <p role="alert" className="rounded-md border border-danger bg-danger-soft p-3 text-[13px] text-danger">
              {models.error.message}
            </p>
          ) : null}
          {models.data ? (
            <Table aria-label="Model registry">
              <THead>
                <TR>
                  <TH>Name</TH>
                  <TH>Provider</TH>
                  <TH>Model id</TH>
                  <TH>Base URL</TH>
                  <TH>Key</TH>
                  <TH>Reasoning</TH>
                  <TH>Capabilities</TH>
                  <TH>Context</TH>
                  <TH>Connection</TH>
                  <TH>Enabled</TH>
                  <TH><span className="sr-only">Actions</span></TH>
                </TR>
              </THead>
              <TBody>
                {models.data.map((model) => (
                  <TR key={model.id}>
                    <TD className="font-medium">{model.display_name}</TD>
                    <TD className="text-secondary">{providerLabel(model.provider_kind)}</TD>
                    <TD className="font-mono text-[12px] text-secondary">
                      {model.litellm_model_name}
                    </TD>
                    <TD className="max-w-48 truncate font-mono text-[12px] text-secondary" title={model.base_url ?? undefined}>
                      {model.base_url ?? '—'}
                    </TD>
                    <TD className="font-mono text-[12px] text-muted">
                      {model.key_fingerprint ?? '—'}
                    </TD>
                    <TD className="capitalize text-secondary">
                      {model.supports_reasoning ? model.default_reasoning_effort : 'off'}
                    </TD>
                    <TD className="text-[11px] text-secondary">
                      {[
                        model.supports_chat_completion ? 'Chat' : null,
                        model.supports_streaming ? 'Stream' : null,
                        model.supports_structured_json ? 'JSON' : null,
                        model.supports_tools ? 'Tools' : null,
                        model.supports_vision ? 'Vision' : null,
                        model.supports_verifier ? 'Judge' : null,
                      ].filter(Boolean).join(' · ') || '—'}
                    </TD>
                    <TD className="font-mono text-[11px] text-secondary">
                      {contextLabel(model.context_window)}
                    </TD>
                    <TD>
                      <div className="space-y-1">
                        <StatusPill tone={model.probe_status === 'passed' ? 'success' : model.probe_status === 'failed' ? 'danger' : 'warning'}>
                          {model.probe_status === 'passed' ? 'Probe passed' : model.probe_status === 'failed' ? 'Probe failed' : 'Probe pending'}
                        </StatusPill>
                        {model.probe_latency_ms !== null ? <p className="text-[10px] text-muted">{model.probe_latency_ms} ms</p> : null}
                        {model.last_probe_error_code ? <p className="font-mono text-[10px] text-danger">{model.last_probe_error_code}</p> : null}
                      </div>
                    </TD>
                    <TD>
                      <input
                        type="checkbox"
                        aria-label={`Enable ${model.display_name}`}
                        checked={model.enabled}
                        disabled={patchModel.isPending}
                        onChange={(event) =>
                          patchModel.mutate(
                            { modelId: model.id, body: { enabled: event.target.checked } },
                            {
                              onSuccess: () => toast.success('Model availability updated'),
                              onError: (error) => toast.error(error.message),
                            },
                          )
                        }
                        className="h-4 w-4 accent-[var(--accent)]"
                      />
                    </TD>
                    <TD className="text-right">
                      <div className="flex justify-end gap-0.5">
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Test ${model.display_name} connection`}
                          disabled={probeModel.isPending}
                          onClick={() => probeModel.mutate(model.id, {
                            onSuccess: () => toast.success('Model connection test queued'),
                            onError: (error) => toast.error(error.message),
                          })}
                        >
                          <RefreshCw className={probeModel.isPending ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} aria-hidden />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Edit ${model.display_name}`}
                          onClick={() => setEditing(model)}
                        >
                          <Pencil className="h-4 w-4" aria-hidden />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label={`Remove ${model.display_name}`}
                          onClick={() => setRemoving(model)}
                        >
                          <Trash2 className="h-4 w-4" aria-hidden />
                        </Button>
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
          {models.data?.length === 0 ? (
            <p className="pt-4 text-center text-[13px] text-secondary">
              No models registered. Add one to enable chat completions.
            </p>
          ) : null}
        </div>
      </div>
      <ModelFormDialog open={addOpen} onOpenChange={setAddOpen} />
      {editing ? (
        <ModelFormDialog
          model={editing}
          open
          onOpenChange={(open) => !open && setEditing(null)}
        />
      ) : null}
      <Dialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <DialogContent
          title="Remove model"
          description={`“${removing?.display_name ?? ''}” will be removed from the registry and every model picker.`}
        >
          <DialogFooter>
            <Button onClick={() => setRemoving(null)}>Cancel</Button>
            <Button
              variant="danger"
              disabled={deleteModel.isPending}
              onClick={() => {
                if (!removing) return;
                deleteModel.mutate(removing.id, {
                  onSuccess: () => {
                    toast.success('Model removed');
                    setRemoving(null);
                  },
                  onError: (error) => toast.error(error.message),
                });
              }}
            >
              {deleteModel.isPending ? 'Removing…' : 'Remove'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
