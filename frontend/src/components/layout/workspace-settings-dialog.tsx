import { useEffect, useState } from 'react';

import type { WorkspaceOut } from '@/api/types';
import { useModels } from '@/features/models/queries';
import { usePatchWorkspace } from '@/features/workspaces/queries';

import { Button } from '../ui/button';
import { Dialog, DialogContent, DialogFooter } from '../ui/dialog';
import { Label } from '../ui/label';
import { NativeSelect } from '../ui/select';
import { Spinner } from '../ui/spinner';
import { toast } from '../ui/toaster';

export function WorkspaceSettingsDialog({
  workspace,
  open,
  onOpenChange,
}: {
  workspace: WorkspaceOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const models = useModels();
  const patchModel = usePatchWorkspace();
  const patchEnrichment = usePatchWorkspace();
  const [modelId, setModelId] = useState(workspace.default_model_id ?? '');
  const [enrichmentEnabled, setEnrichmentEnabled] = useState(
    workspace.enrichment_enabled,
  );

  useEffect(() => {
    if (open) {
      setModelId(workspace.default_model_id ?? '');
      setEnrichmentEnabled(workspace.enrichment_enabled);
    }
  }, [open, workspace.default_model_id, workspace.enrichment_enabled, workspace.id]);

  const close = (next: boolean): void => {
    if (!next) {
      patchModel.reset();
      patchEnrichment.reset();
    }
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title="Workspace settings"
        description={`Choose the default completion model for ${workspace.name}.`}
      >
        <div className="space-y-3">
          <div>
            <Label htmlFor="workspace-default-model">Default model</Label>
            <NativeSelect
              id="workspace-default-model"
              value={modelId}
              disabled={models.isPending || patchModel.isPending}
              onChange={(event) => setModelId(event.target.value)}
            >
              <option value="">Automatic — first enabled model</option>
              {(models.data ?? []).map((model) => (
                <option key={model.id} value={model.id}>
                  {model.display_name}
                </option>
              ))}
            </NativeSelect>
          </div>
          {models.isPending ? <Spinner label="Loading models…" /> : null}
          {models.isError ? (
            <p role="alert" className="text-[12px] text-danger">
              Unable to load enabled models.
            </p>
          ) : null}
          {patchModel.isError ? (
            <p role="alert" className="text-[12px] text-danger">
              {patchModel.error.message}
            </p>
          ) : null}
          <p className="text-[12px] text-secondary">
            Automatic selection uses the first enabled model when a chat does not choose one.
          </p>
          <div className="rounded-lg border border-line bg-subtle/40 p-3">
            <label className="flex items-start gap-3" htmlFor="workspace-enrichment-enabled">
              <input
                id="workspace-enrichment-enabled"
                type="checkbox"
                aria-label="Enrich approved documents"
                className="mt-0.5 h-4 w-4 accent-primary"
                checked={enrichmentEnabled}
                disabled={patchEnrichment.isPending}
                onChange={(event) => setEnrichmentEnabled(event.target.checked)}
              />
              <span>
                <span className="block text-[13px] font-medium text-ink">
                  Enrich approved documents
                </span>
                <span className="mt-1 block text-[12px] text-secondary">
                  Generate bounded summaries, keywords, and retrieval questions in the
                  background using the measured utility model. Existing approved documents are
                  backfilled asynchronously.
                </span>
              </span>
            </label>
            {patchEnrichment.isError ? (
              <p role="alert" className="mt-2 text-[12px] text-danger">
                {patchEnrichment.error.message}
              </p>
            ) : null}
            <div className="mt-3 flex justify-end">
              <Button
                disabled={
                  patchEnrichment.isPending ||
                  enrichmentEnabled === workspace.enrichment_enabled
                }
                onClick={() =>
                  patchEnrichment.mutate(
                    {
                      workspaceId: workspace.id,
                      body: { enrichment_enabled: enrichmentEnabled },
                    },
                    {
                      onSuccess: () => toast.success('Document enrichment setting updated'),
                    },
                  )
                }
              >
                {patchEnrichment.isPending ? 'Applying…' : 'Apply enrichment setting'}
              </Button>
            </div>
          </div>
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button
              variant="primary"
              disabled={models.isPending || patchModel.isPending}
              onClick={() =>
                patchModel.mutate(
                  {
                    workspaceId: workspace.id,
                    body: { default_model_id: modelId || null },
                  },
                  {
                    onSuccess: () => {
                      toast.success('Workspace default model updated');
                      close(false);
                    },
                  },
                )
              }
            >
              {patchModel.isPending ? 'Saving…' : 'Save changes'}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
