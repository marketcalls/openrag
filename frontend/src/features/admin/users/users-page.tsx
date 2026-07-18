import { UserPlus } from 'lucide-react';
import { useState } from 'react';

import type { UserOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { InviteDialog } from './invite-dialog';
import { usePatchUser, useUsers, type ManagedRole } from './queries';

export function UsersPage() {
  const users = useUsers();
  const patchUser = usePatchUser();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [confirmUser, setConfirmUser] = useState<UserOut | null>(null);

  const updateUser = (userId: string, body: { active?: boolean; role?: ManagedRole }) => {
    patchUser.mutate(
      { userId, body },
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
        actions={
          <Button variant="primary" size="sm" onClick={() => setInviteOpen(true)}>
            <UserPlus className="h-3.5 w-3.5" aria-hidden /> Invite
          </Button>
        }
      />
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto max-w-4xl">
          {users.isPending ? <Spinner label="Loading users…" /> : null}
          {users.isError ? (
            <p role="alert" className="rounded-md border border-danger bg-danger-soft p-3 text-[13px] text-danger">
              {users.error.message}
            </p>
          ) : null}
          {users.data ? (
            <Table aria-label="Organization users">
              <THead>
                <TR>
                  <TH>Email</TH>
                  <TH>Role</TH>
                  <TH>Status</TH>
                  <TH><span className="sr-only">Actions</span></TH>
                </TR>
              </THead>
              <TBody>
                {users.data.map((user) => (
                  <TR key={user.id}>
                    <TD className="font-medium">{user.email}</TD>
                    <TD>
                      {user.role === 'superadmin' ? (
                        <span className="text-secondary">Superadmin</span>
                      ) : (
                        <NativeSelect
                          aria-label={`Role for ${user.email}`}
                          className="w-28"
                          value={user.role}
                          disabled={patchUser.isPending}
                          onChange={(event) =>
                            updateUser(user.id, { role: event.target.value as ManagedRole })
                          }
                        >
                          <option value="user">User</option>
                          <option value="admin">Admin</option>
                        </NativeSelect>
                      )}
                    </TD>
                    <TD>
                      <StatusPill tone={user.active ? 'success' : 'danger'}>
                        {user.active ? 'Active' : 'Deactivated'}
                      </StatusPill>
                    </TD>
                    <TD className="text-right">
                      {user.role !== 'superadmin' ? (
                        <Button size="sm" onClick={() => setConfirmUser(user)}>
                          {user.active ? 'Deactivate' : 'Reactivate'}
                        </Button>
                      ) : null}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
          {users.data?.length === 0 ? (
            <p className="pt-4 text-center text-[13px] text-secondary">No users found.</p>
          ) : null}
        </div>
      </div>
      <InviteDialog open={inviteOpen} onOpenChange={setInviteOpen} />
      <Dialog open={confirmUser !== null} onOpenChange={(open) => !open && setConfirmUser(null)}>
        <DialogContent
          title={confirmUser?.active ? 'Deactivate user' : 'Reactivate user'}
          description={
            confirmUser?.active
              ? `${confirmUser.email} will immediately lose access.`
              : `${confirmUser?.email ?? ''} will regain access.`
          }
        >
          <DialogFooter>
            <Button onClick={() => setConfirmUser(null)}>Cancel</Button>
            <Button
              variant={confirmUser?.active ? 'danger' : 'primary'}
              disabled={patchUser.isPending}
              onClick={() => {
                if (confirmUser) {
                  updateUser(confirmUser.id, { active: !confirmUser.active });
                }
                setConfirmUser(null);
              }}
            >
              Confirm
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
