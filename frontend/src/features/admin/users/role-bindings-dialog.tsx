import { useEffect, useMemo, useState, type FormEvent } from 'react';

import type { UserOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';
import { useReplaceRoleBindings, useRoles } from '@/features/admin/roles/queries';

export function RoleBindingsDialog({
  user,
  open,
  onOpenChange,
}: {
  user: UserOut;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const roles = useRoles();
  const replace = useReplaceRoleBindings();
  const resetReplace = replace.reset;
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const assignable = useMemo(
    () => (roles.data ?? []).filter((role) => role.is_assignable && role.key !== 'platform_superadmin'),
    [roles.data],
  );

  useEffect(() => {
    if (open) {
      setSelected(new Set(user.roles.map((role) => role.id)));
      resetReplace();
    }
  }, [open, resetReplace, user]);

  const close = (next: boolean): void => {
    if (!next && !replace.isPending) onOpenChange(false);
  };

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    replace.mutate(
      { userId: user.id, body: { role_ids: [...selected] } },
      {
        onSuccess: () => {
          toast.success('Role bindings updated');
          onOpenChange(false);
        },
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title="Manage organization roles"
        description={`Replace the effective organization roles for ${user.email}. Workspace membership is managed separately.`}
      >
        <form onSubmit={submit}>
          {roles.isPending ? <Spinner label="Loading assignable roles…" /> : null}
          {roles.isError ? <p role="alert" className="text-[12px] text-danger">{roles.error.message}</p> : null}
          <fieldset className="space-y-px overflow-hidden rounded-md border border-line bg-line">
            <legend className="sr-only">Assignable roles</legend>
            {assignable.map((role) => (
              <label key={role.id} className="flex cursor-pointer items-start gap-3 bg-bg p-3 hover:bg-raised">
                <input
                  type="checkbox"
                  className="mt-0.5 h-4 w-4 accent-accent"
                  checked={selected.has(role.id)}
                  disabled={replace.isPending}
                  onChange={(event) => {
                    const next = new Set(selected);
                    if (event.target.checked) next.add(role.id);
                    else next.delete(role.id);
                    setSelected(next);
                  }}
                />
                <span>
                  <span className="block text-[13px] font-medium text-ink">{role.name} · {role.permissions.length} capabilities</span>
                  <span className="mt-0.5 block text-[11px] text-secondary">{role.description}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {roles.data && assignable.length === 0 ? <p className="py-4 text-[12px] text-secondary">No assignable roles are available.</p> : null}
          {replace.isError ? <p role="alert" className="mt-3 border-l-2 border-danger bg-danger-soft px-3 py-2 text-[12px] text-danger">{replace.error.message}</p> : null}
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={roles.isPending || roles.isError || replace.isPending}>
              {replace.isPending ? 'Saving…' : 'Save roles'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
