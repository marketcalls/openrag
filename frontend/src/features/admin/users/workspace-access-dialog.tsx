import { useEffect, useState } from 'react';

import type { UserOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { toast } from '@/components/ui/toaster';
import {
  useAddWorkspaceMember,
  useWorkspaceMembers,
  useWorkspaces,
} from '@/features/workspaces/queries';

export function WorkspaceAccessDialog({
  user,
  open,
  onOpenChange,
}: {
  user: UserOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const workspaces = useWorkspaces();
  const [workspaceId, setWorkspaceId] = useState('');
  const members = useWorkspaceMembers(workspaceId || null);
  const addMember = useAddWorkspaceMember();

  useEffect(() => {
    if (open && !workspaceId && workspaces.data?.[0]) {
      setWorkspaceId(workspaces.data[0].id);
    }
  }, [open, workspaceId, workspaces.data]);

  const isMember =
    members.data?.some((membership) => membership.user_id === user.id) ?? false;

  const close = (next: boolean): void => {
    if (!next) {
      setWorkspaceId('');
      addMember.reset();
    }
    onOpenChange(next);
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title="Workspace access"
        description={`Grant ${user.email} access to an OpenRAG workspace.`}
      >
        <div className="space-y-3">
          <div>
            <Label htmlFor="access-workspace">Workspace</Label>
            <NativeSelect
              id="access-workspace"
              value={workspaceId}
              disabled={workspaces.isPending || workspaces.data?.length === 0}
              onChange={(event) => setWorkspaceId(event.target.value)}
            >
              <option value="">Select a workspace</option>
              {(workspaces.data ?? []).map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </NativeSelect>
          </div>

          {workspaces.isPending || (workspaceId && members.isPending) ? (
            <Spinner label="Checking workspace access…" />
          ) : null}
          {workspaces.isError || members.isError ? (
            <p role="alert" className="text-[12px] text-danger">
              Unable to load workspace access.
            </p>
          ) : null}
          {workspaces.data?.length === 0 ? (
            <p className="text-[12px] text-secondary">
              Create a workspace before assigning access.
            </p>
          ) : null}
          {workspaceId && members.data ? (
            <div className="flex items-center justify-between rounded-md border border-line bg-raised px-3 py-2">
              <span className="text-[13px] text-secondary">Membership</span>
              {isMember ? (
                <StatusPill tone="success">Already a member</StatusPill>
              ) : (
                <StatusPill tone="warning">No access</StatusPill>
              )}
            </div>
          ) : null}

          <DialogFooter>
            <Button onClick={() => close(false)}>Close</Button>
            {workspaceId && members.data && !isMember ? (
              <Button
                variant="primary"
                disabled={addMember.isPending}
                onClick={() =>
                  addMember.mutate(
                    { workspaceId, userId: user.id },
                    {
                      onSuccess: () =>
                        toast.success(`${user.email} can now access this workspace`),
                      onError: (error) => toast.error(error.message),
                    },
                  )
                }
              >
                {addMember.isPending ? 'Granting…' : 'Grant access'}
              </Button>
            ) : null}
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}
