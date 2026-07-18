import { useState, type FormEvent } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { NativeSelect } from '@/components/ui/select';
import { toast } from '@/components/ui/toaster';

import { useInvite, type ManagedRole } from './queries';

export function InviteDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const invite = useInvite();
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<ManagedRole>('user');

  const close = (next: boolean): void => {
    if (!next) {
      invite.reset();
      setEmail('');
      setRole('user');
    }
    onOpenChange(next);
  };

  const onSubmit = (event: FormEvent): void => {
    event.preventDefault();
    invite.mutate({ email: email.trim(), role });
  };

  const inviteLink = invite.data
    ? `${window.location.origin}/invite?token=${invite.data.invite_token}`
    : null;

  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent
        title="Invite a user"
        description="Copy this one-time invitation link and send it securely to the new user."
      >
        {inviteLink ? (
          <div className="space-y-3">
            <p className="break-all rounded-md border border-line bg-subtle p-2 font-mono text-[12px] text-ink">
              {inviteLink}
            </p>
            <p className="text-[12px] text-secondary">
              This token is shown once. Closing the dialog clears it from the screen.
            </p>
            <DialogFooter>
              <Button
                variant="primary"
                onClick={() => {
                  void navigator.clipboard
                    .writeText(inviteLink)
                    .then(() => toast.success('Invite link copied'))
                    .catch(() => toast.error('Could not copy the invite link'));
                }}
              >
                Copy link
              </Button>
            </DialogFooter>
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
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </div>
            <div>
              <Label htmlFor="invite-role">Role</Label>
              <NativeSelect
                id="invite-role"
                value={role}
                onChange={(event) => setRole(event.target.value as ManagedRole)}
              >
                <option value="user">User</option>
                <option value="admin">Admin</option>
              </NativeSelect>
            </div>
            {invite.isError ? (
              <p role="alert" className="text-[12px] text-danger">
                {invite.error.message}
              </p>
            ) : null}
            <DialogFooter>
              <Button onClick={() => close(false)}>Cancel</Button>
              <Button type="submit" variant="primary" disabled={invite.isPending}>
                {invite.isPending ? 'Creating…' : 'Send invite'}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
