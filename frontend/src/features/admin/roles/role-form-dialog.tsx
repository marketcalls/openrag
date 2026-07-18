import { ShieldCheck } from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent } from 'react';

import type { PermissionCatalogOut, PermissionCode, RoleOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';

import { useCreateRole, usePatchRole, usePermissionCatalog } from './queries';

export function RoleFormDialog({
  open,
  onOpenChange,
  role,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  role?: RoleOut | null;
}) {
  const catalog = usePermissionCatalog(open);
  const createRole = useCreateRole();
  const patchRole = usePatchRole();
  const resetCreateRole = createRole.reset;
  const resetPatchRole = patchRole.reset;
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [permissions, setPermissions] = useState<Set<PermissionCode>>(new Set());
  const mutation = role ? patchRole : createRole;

  useEffect(() => {
    if (!open) return;
    setName(role?.name ?? '');
    setDescription(role?.description ?? '');
    setPermissions(new Set(role?.permissions ?? []));
    resetCreateRole();
    resetPatchRole();
  }, [open, resetCreateRole, resetPatchRole, role]);

  const groups = useMemo(() => {
    const grouped = new Map<string, PermissionCatalogOut[]>();
    for (const permission of catalog.data ?? []) {
      grouped.set(permission.group, [...(grouped.get(permission.group) ?? []), permission]);
    }
    return [...grouped.entries()];
  }, [catalog.data]);

  const close = (next: boolean): void => {
    if (!next && !mutation.isPending) onOpenChange(false);
  };

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    const body = {
      name: name.trim(),
      description: description.trim(),
      permissions: [...permissions].sort(),
    };
    const options = {
      onSuccess: () => {
        toast.success(role ? 'Role updated' : 'Role created');
        onOpenChange(false);
      },
    };
    if (role) patchRole.mutate({ roleId: role.id, body }, options);
    else createRole.mutate(body, options);
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        className="max-h-[min(90vh,760px)] max-w-2xl overflow-y-auto"
        title={role ? `Edit ${role.name}` : 'Create a custom role'}
        description="Choose capabilities from the server-managed catalog. Access is always enforced by OpenRAG on the server."
      >
        <form onSubmit={submit} className="space-y-5">
          {role?.is_system ? (
            <div className="flex items-start gap-2 border-l-2 border-accent bg-accent-soft px-3 py-2 text-[12px] text-accent-on-soft">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
              <p>This system role is protected. Its identity cannot be renamed or deleted.</p>
            </div>
          ) : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <Label htmlFor="role-name">Role name</Label>
              <Input
                id="role-name"
                required
                minLength={2}
                maxLength={80}
                disabled={role?.is_system || mutation.isPending}
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="role-description">Description</Label>
              <Input
                id="role-description"
                maxLength={500}
                disabled={mutation.isPending}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
            </div>
          </div>

          <fieldset>
            <legend className="text-[13px] font-semibold text-ink">Capabilities</legend>
            <p className="mt-0.5 text-[12px] text-secondary">
              Select the smallest set this role needs. Changes take effect on the next request.
            </p>
            {catalog.isPending ? <Spinner label="Loading capability catalog…" /> : null}
            {catalog.isError ? (
              <p role="alert" className="mt-3 border-l-2 border-danger bg-danger-soft px-3 py-2 text-[12px] text-danger">
                {catalog.error.message}
              </p>
            ) : null}
            <div className="mt-3 space-y-4">
              {groups.map(([group, items]) => (
                <section key={group} aria-labelledby={`capability-${group}`}>
                  <h3 id={`capability-${group}`} className="mb-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
                    {group}
                  </h3>
                  <div className="grid gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-2">
                    {items.map((permission) => {
                      const checked = permissions.has(permission.code);
                      return (
                        <label
                          key={permission.code}
                          className="flex cursor-pointer gap-2.5 bg-bg p-3 transition-colors hover:bg-raised motion-reduce:transition-none"
                        >
                          <input
                            type="checkbox"
                            className="mt-0.5 h-4 w-4 shrink-0 accent-accent"
                            checked={checked}
                            disabled={mutation.isPending}
                            onChange={(event) => {
                              const next = new Set(permissions);
                              if (event.target.checked) next.add(permission.code);
                              else next.delete(permission.code);
                              setPermissions(next);
                            }}
                          />
                          <span>
                            <span className="block text-[13px] font-medium text-ink">{permission.label}</span>
                            <span className="mt-0.5 block text-[11px] leading-relaxed text-secondary">{permission.description}</span>
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </section>
              ))}
            </div>
          </fieldset>

          {mutation.isError ? (
            <p role="alert" className="border-l-2 border-danger bg-danger-soft px-3 py-2 text-[12px] text-danger">
              {mutation.error.message}
            </p>
          ) : null}
          <DialogFooter>
            <Button onClick={() => close(false)} disabled={mutation.isPending}>Cancel</Button>
            <Button type="submit" variant="primary" disabled={catalog.isPending || catalog.isError || mutation.isPending}>
              {mutation.isPending ? 'Saving…' : role ? 'Save changes' : 'Create role'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
