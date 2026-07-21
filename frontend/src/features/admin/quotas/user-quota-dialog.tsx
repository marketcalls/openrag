import { type FormEvent, useState } from 'react';

import type { UserOut, UserQuotaOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';

import { useSetUserQuota, useUserQuota } from './queries';

function UserQuotaForm({
  userId,
  quota,
  onDone,
}: {
  userId: string;
  quota: UserQuotaOut;
  onDone: () => void;
}) {
  const save = useSetUserQuota();
  const [override, setOverride] = useState(
    quota.monthly_tokens == null ? '' : String(quota.monthly_tokens),
  );
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(
      { userId, monthlyTokens: override === '' ? null : Number(override) },
      {
        onSuccess: () => {
          toast.success('User token budget saved');
          onDone();
        },
        onError: (error) => toast.error(error.message),
      },
    );
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <p className="text-[12px] text-secondary">
        Used {quota.used_tokens.toLocaleString()} /{' '}
        {quota.allocated_tokens == null ? 'unlimited' : quota.allocated_tokens.toLocaleString()}{' '}
        tokens this period · resets {new Date(quota.resets_at).toLocaleDateString()}
      </p>
      <div>
        <Label htmlFor="user-token-override">Monthly token override</Label>
        <Input
          id="user-token-override"
          type="number"
          min={0}
          placeholder="Use organization default"
          value={override}
          onChange={(event) => setOverride(event.target.value)}
        />
      </div>
      <DialogFooter>
        <Button type="button" onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>Save budget</Button>
      </DialogFooter>
    </form>
  );
}

export function UserQuotaDialog({
  user,
  onOpenChange,
}: {
  user: UserOut | null;
  onOpenChange: (open: boolean) => void;
}) {
  const quota = useUserQuota(user?.id ?? '', user !== null);
  return (
    <Dialog open={user !== null} onOpenChange={onOpenChange}>
      <DialogContent
        title={user ? `Token budget — ${user.email}` : 'User token budget'}
        description="Set a monthly override, or leave it blank to inherit the organization default."
      >
        {quota.isPending ? <Spinner label="Loading token usage…" /> : null}
        {quota.isError ? <p role="alert" className="text-[12px] text-danger">{quota.error.message}</p> : null}
        {user && quota.data ? (
          <UserQuotaForm
            userId={user.id}
            quota={quota.data}
            onDone={() => onOpenChange(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
