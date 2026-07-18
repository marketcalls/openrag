import { KeyRound, Pencil, Plus, ShieldCheck, Trash2 } from 'lucide-react';
import { useState } from 'react';

import type { RoleOut } from '@/api/types';
import { TopBar } from '@/components/layout/top-bar';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { StatusPill } from '@/components/ui/status-pill';
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table';
import { toast } from '@/components/ui/toaster';

import { useDeleteRole, useRoles } from './queries';
import { RoleFormDialog } from './role-form-dialog';

export function RolesPage() {
  const roles = useRoles();
  const deleteRole = useDeleteRole();
  const [formRole, setFormRole] = useState<RoleOut | 'new' | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<RoleOut | null>(null);
  const systemCount = roles.data?.filter((role) => role.is_system).length ?? 0;
  const customCount = roles.data?.filter((role) => !role.is_system).length ?? 0;

  return (
    <>
      <TopBar
        title="Roles & access"
        actions={
          <Button variant="primary" size="sm" onClick={() => setFormRole('new')}>
            <Plus className="h-3.5 w-3.5" aria-hidden /> Create role
          </Button>
        }
      />
      <main className="flex-1 overflow-y-auto p-3 sm:p-5">
        <div className="mx-auto max-w-5xl">
          <div className="mb-5 grid border-y border-line sm:grid-cols-[1fr_auto_auto]">
            <div className="py-4 sm:pr-6">
              <div className="flex items-center gap-2 text-[12px] font-medium text-accent-on-soft">
                <KeyRound className="h-4 w-4" aria-hidden /> Capability access
              </div>
              <p className="mt-1 max-w-xl text-[13px] leading-relaxed text-secondary">
                Compose least-privilege roles from audited capabilities. Platform access is managed outside organization roles.
              </p>
            </div>
            <div className="border-t border-line py-3 sm:border-l sm:border-t-0 sm:px-6 sm:py-4">
              <span className="block text-xl font-semibold tabular-nums text-ink">{systemCount}</span>
              <span className="text-[11px] uppercase tracking-[0.1em] text-muted">Protected</span>
            </div>
            <div className="border-t border-line py-3 sm:border-l sm:border-t-0 sm:pl-6 sm:py-4">
              <span className="block text-xl font-semibold tabular-nums text-ink">{customCount}</span>
              <span className="text-[11px] uppercase tracking-[0.1em] text-muted">Custom</span>
            </div>
          </div>

          {roles.isPending ? <Spinner label="Loading roles…" /> : null}
          {roles.isError ? (
            <p role="alert" className="border-l-2 border-danger bg-danger-soft p-3 text-[13px] text-danger">{roles.error.message}</p>
          ) : null}
          {deleteRole.isError ? (
            <p role="alert" className="mb-3 border-l-2 border-danger bg-danger-soft p-3 text-[13px] text-danger">{deleteRole.error.message}</p>
          ) : null}
          {roles.data && roles.data.length > 0 ? (
            <Table aria-label="Organization roles">
              <THead>
                <TR><TH>Role</TH><TH>Capabilities</TH><TH>Type</TH><TH><span className="sr-only">Actions</span></TH></TR>
              </THead>
              <TBody>
                {roles.data.map((role) => (
                  <TR key={role.id}>
                    <TD className="min-w-56">
                      <div className="font-medium text-ink">{role.name}</div>
                      <div className="mt-0.5 max-w-md text-[12px] text-secondary">{role.description || 'No description provided.'}</div>
                    </TD>
                    <TD className="whitespace-nowrap">{role.permissions.length} {role.permissions.length === 1 ? 'permission' : 'permissions'}</TD>
                    <TD>{role.is_system ? <StatusPill tone="accent"><ShieldCheck className="mr-1 h-3 w-3" aria-hidden />Protected</StatusPill> : <span className="text-secondary">Custom</span>}</TD>
                    <TD>
                      <div className="flex justify-end gap-1">
                        <Button size="icon" aria-label={`Edit ${role.name}`} onClick={() => setFormRole(role)}><Pencil className="h-3.5 w-3.5" aria-hidden /></Button>
                        {!role.is_system ? <Button size="icon" aria-label={`Delete ${role.name}`} onClick={() => setDeleteTarget(role)}><Trash2 className="h-3.5 w-3.5" aria-hidden /></Button> : null}
                      </div>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          ) : null}
          {roles.data?.length === 0 ? (
            <div className="border-y border-dashed border-line py-12 text-center">
              <ShieldCheck className="mx-auto h-6 w-6 text-muted" aria-hidden />
              <h2 className="mt-3 text-[14px] font-semibold text-ink">No roles are available</h2>
              <p className="mt-1 text-[12px] text-secondary">Create a custom role or ask an operator to restore the built-in templates.</p>
            </div>
          ) : null}
        </div>
      </main>

      <RoleFormDialog
        open={formRole !== null}
        role={formRole === 'new' ? null : formRole}
        onOpenChange={(open) => !open && setFormRole(null)}
      />
      <Dialog open={deleteTarget !== null} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <DialogContent title="Delete custom role" description={`${deleteTarget?.name ?? 'This role'} can be deleted only when it has no user bindings.`}>
          <DialogFooter>
            <Button onClick={() => setDeleteTarget(null)}>Cancel</Button>
            <Button
              variant="danger"
              disabled={deleteRole.isPending}
              onClick={() => {
                if (!deleteTarget) return;
                deleteRole.mutate(deleteTarget.id, {
                  onSuccess: () => {
                    toast.success('Role deleted');
                    setDeleteTarget(null);
                  },
                });
              }}
            >
              {deleteRole.isPending ? 'Deleting…' : 'Delete role'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
