import { Check, ChevronsUpDown, Plus } from 'lucide-react';
import { useState, type FormEvent } from 'react';

import { useCreateWorkspace, useWorkspaces } from '@/features/workspaces/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';
import { useClaims } from '@/lib/use-claims';

import { Button } from '../ui/button';
import { Dialog, DialogContent, DialogFooter } from '../ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '../ui/dropdown-menu';
import { Input } from '../ui/input';
import { Label } from '../ui/label';

export function WorkspaceSwitcher() {
  const claims = useClaims();
  const { data: workspaces } = useWorkspaces();
  const { workspaceId, setWorkspaceId } = useWorkspace();
  const createWorkspace = useCreateWorkspace();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [name, setName] = useState('');

  const current = workspaces?.find((workspace) => workspace.id === workspaceId);
  const canCreate = claims?.role === 'admin' || claims?.role === 'superadmin';

  const onCreate = (event: FormEvent) => {
    event.preventDefault();
    createWorkspace.mutate(
      { name },
      {
        onSuccess: (workspace) => {
          setWorkspaceId(workspace.id);
          setName('');
          setDialogOpen(false);
        },
      },
    );
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-[13px] font-medium text-ink hover:bg-subtle"
            aria-label="Switch workspace"
          >
            <span className="truncate">{current?.name ?? 'Select workspace'}</span>
            <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          {(workspaces ?? []).map((workspace) => (
            <DropdownMenuItem
              key={workspace.id}
              onSelect={() => setWorkspaceId(workspace.id)}
            >
              <span className="flex-1 truncate">{workspace.name}</span>
              {workspace.id === workspaceId ? (
                <Check className="h-3.5 w-3.5 text-accent" aria-hidden />
              ) : null}
            </DropdownMenuItem>
          ))}
          {canCreate ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setDialogOpen(true)}>
                <Plus className="mr-1 h-3.5 w-3.5" aria-hidden /> New workspace
              </DropdownMenuItem>
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent title="New workspace">
          <form onSubmit={onCreate}>
            <Label htmlFor="workspace-name">Name</Label>
            <Input
              id="workspace-name"
              required
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
            <DialogFooter>
              <Button onClick={() => setDialogOpen(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={createWorkspace.isPending}>
                Create
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
