import { CheckCircle2 } from 'lucide-react';
import { useEffect, useMemo, useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { Spinner } from '@/components/ui/spinner';
import { useRoles } from '@/features/admin/roles/queries';

import { useInvite } from './queries';

export function InviteDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const invite = useInvite();
  const roles = useRoles();
  const [email, setEmail] = useState('');
  const [roleId, setRoleId] = useState('');
  const assignableRoles = useMemo(
    () => (roles.data ?? []).filter((role) => role.is_assignable && role.key !== 'platform_superadmin'),
    [roles.data],
  );

  useEffect(() => {
    if (open && !roleId && assignableRoles[0]) setRoleId(assignableRoles[0].id);
  }, [assignableRoles, open, roleId]);

  const close = (next: boolean): void => {
    if (!next) {
      invite.reset();
      setEmail('');
      setRoleId('');
    }
    onOpenChange(next);
  };

  const onSubmit = (event: FormEvent): void => {
    event.preventDefault();
    if (roleId) invite.mutate({ email: email.trim(), role_id: roleId });
  };

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title="Invite a user"
        description="Assign an organization role. OpenRAG sends the one-time credential through the configured secure delivery channel."
      >
        {invite.isSuccess ? (
          <div className="border-y border-line py-5 text-center">
            <CheckCircle2 className="mx-auto h-7 w-7 text-success" aria-hidden />
            <h3 className="mt-2 text-[14px] font-semibold text-ink">Invitation queued</h3>
            <p className="mt-1 text-[12px] leading-relaxed text-secondary">
              If the address is eligible, delivery will continue out of band. No invitation secret is shown here.
            </p>
            <DialogFooter><Button variant="primary" onClick={() => close(false)}>Done</Button></DialogFooter>
          </div>
        ) : (
          <form onSubmit={onSubmit} className="space-y-3">
            <div>
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                autoComplete="email"
                required
                disabled={invite.isPending}
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="invite-role">Role</Label>
              {roles.isPending ? <Spinner label="Loading assignable roles…" /> : null}
              <NativeSelect
                id="invite-role"
                required
                value={roleId}
                disabled={roles.isPending || roles.isError || assignableRoles.length === 0 || invite.isPending}
                onChange={(event) => setRoleId(event.target.value)}
              >
                <option value="">Select a role</option>
                {assignableRoles.map((role) => <option key={role.id} value={role.id}>{role.name}</option>)}
              </NativeSelect>
            </div>
            {roles.isError ? <p role="alert" className="text-[12px] text-danger">{roles.error.message}</p> : null}
            {roles.data && assignableRoles.length === 0 ? <p className="text-[12px] text-secondary">No assignable organization roles are available.</p> : null}
            {invite.isError ? <p role="alert" className="text-[12px] text-danger">{invite.error.message}</p> : null}
            <DialogFooter>
              <Button onClick={() => close(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={!roleId || invite.isPending}>
                {invite.isPending ? 'Queuing…' : 'Send invite'}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
