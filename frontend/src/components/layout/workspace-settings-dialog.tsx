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
  const patchWorkspace = usePatchWorkspace();
  const [modelId, setModelId] = useState(workspace.default_model_id ?? '');

  useEffect(() => {
    if (open) setModelId(workspace.default_model_id ?? '');
  }, [open, workspace.default_model_id, workspace.id]);

  const close = (next: boolean): void => {
    if (!next) patchWorkspace.reset();
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
              disabled={models.isPending || patchWorkspace.isPending}
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
          {patchWorkspace.isError ? (
            <p role="alert" className="text-[12px] text-danger">
              {patchWorkspace.error.message}
            </p>
          ) : null}
          <p className="text-[12px] text-secondary">
            Automatic selection uses the first enabled model when a chat does not choose one.
          </p>
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button
              variant="primary"
              disabled={models.isPending || patchWorkspace.isPending}
              onClick={() =>
                patchWorkspace.mutate(
                  {
                    workspaceId: workspace.id,
                    defaultModelId: modelId || null,
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
              {patchWorkspace.isPending ? 'Saving…' : 'Save changes'}
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
