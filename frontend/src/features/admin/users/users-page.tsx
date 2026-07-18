import { FolderKey, KeyRound, UserPlus } from 'lucide-react';
import { useState } from 'react';

import type { UserOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';
import { hasPermission } from '@/lib/jwt';
import { useClaims } from '@/lib/use-claims';

import { InviteDialog } from './invite-dialog';
import { usePatchUser, useUsers } from './queries';
import { RoleBindingsDialog } from './role-bindings-dialog';
import { WorkspaceAccessDialog } from './workspace-access-dialog';

export function UsersPage() {
  const claims = useClaims();
  const users = useUsers();
  const patchUser = usePatchUser();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [confirmUser, setConfirmUser] = useState<UserOut | null>(null);
  const [accessUser, setAccessUser] = useState<UserOut | null>(null);
  const [roleUser, setRoleUser] = useState<UserOut | null>(null);
  const canManageRoles = claims ? hasPermission(claims, 'role.manage') : false;
  const canManageWorkspaces = claims ? hasPermission(claims, 'workspace.manage') : false;

  const updateActive = (userId: string, active: boolean) => {
    patchUser.mutate(
      { userId, body: { active } },
      {
        onSuccess: () => toast.success('User updated'),
        onError: (error) => toast.error(error.message),
      },
    );
  };

  return (
    <>
      <TopBar
        title="Users"
        actions={canManageRoles ? <Button variant="primary" size="sm" onClick={() => setInviteOpen(true)}><UserPlus className="h-3.5 w-3.5" aria-hidden /> Invite</Button> : null}
      />
      <main className="flex-1 overflow-y-auto p-3 sm:p-5">
        <div className="mx-auto max-w-5xl">
          <div className="mb-4 border-y border-line py-3">
            <p className="text-[13px] text-secondary">Organization roles control capabilities; workspace access controls which knowledge each person can reach.</p>
          </div>
          {users.isPending ? <Spinner label="Loading users…" /> : null}
          {users.isError ? <p role="alert" className="border-l-2 border-danger bg-danger-soft p-3 text-[13px] text-danger">{users.error.message}</p> : null}
          {users.data ? (
            <Table aria-label="Organization users">
              <THead><TR><TH>User</TH><TH>Effective roles</TH><TH>Status</TH><TH><span className="sr-only">Actions</span></TH></TR></THead>
              <TBody>
                {users.data.map((user) => (
                  <TR key={user.id}>
                    <TD className="font-medium">{user.email}</TD>
                    <TD>
                      <div className="flex max-w-sm flex-wrap gap-1">
                        {user.is_platform_superadmin ? <StatusPill tone="warning">Platform superadmin</StatusPill> : null}
                        {user.roles.map((role) => <StatusPill key={role.id} tone={role.is_system ? 'accent' : 'success'}>{role.name}</StatusPill>)}
                        {!user.is_platform_superadmin && user.roles.length === 0 ? <span className="text-[12px] text-muted">No role bindings</span> : null}
                      </div>
                    </TD>
                    <TD><StatusPill tone={user.active ? 'success' : 'danger'}>{user.active ? 'Active' : 'Deactivated'}</StatusPill></TD>
                    <TD className="text-right">
                      {!user.is_platform_superadmin ? (
                        <div className="flex flex-wrap justify-end gap-1.5">
                          {canManageRoles ? <Button size="sm" aria-label={`Manage roles for ${user.email}`} onClick={() => setRoleUser(user)}><KeyRound className="h-3.5 w-3.5" aria-hidden /> Roles</Button> : null}
                          {canManageWorkspaces ? <Button size="sm" onClick={() => setAccessUser(user)}><FolderKey className="h-3.5 w-3.5" aria-hidden /> Workspace access</Button> : null}
                          <Button size="sm" onClick={() => setConfirmUser(user)}>{user.active ? 'Deactivate' : 'Reactivate'}</Button>
                        </div>
                      ) : <span className="text-[11px] text-muted">Platform-managed</span>}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
          {users.data?.length === 0 ? <div className="border-y border-dashed border-line py-12 text-center"><h2 className="text-[14px] font-semibold text-ink">No users found</h2><p className="mt-1 text-[12px] text-secondary">Invite the first organization user to begin assigning access.</p></div> : null}
        </div>
      </main>
      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />
      {roleUser ? <RoleBindingsDialog user={roleUser} open onOpenChange={(open) => !open && setRoleUser(null)} /> : null}
      {accessUser ? <WorkspaceAccessDialog user={accessUser} open onOpenChange={(open) => !open && setAccessUser(null)} /> : null}
      <Dialog open={confirmUser !== null} onOpenChange={(open) => !open && setConfirmUser(null)}>
        <DialogContent title={confirmUser?.active ? 'Deactivate user' : 'Reactivate user'} description={confirmUser?.active ? `${confirmUser.email} will immediately lose access.` : `${confirmUser?.email ?? ''} will regain access.`}>
          <DialogFooter>
            <Button onClick={() => setConfirmUser(null)}>Cancel</Button>
            <Button
              variant={confirmUser?.active ? 'danger' : 'primary'}
              disabled={patchUser.isPending}
              onClick={() => {
                if (confirmUser) updateActive(confirmUser.id, !confirmUser.active);
                setConfirmUser(null);
              }}
            >Confirm</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
