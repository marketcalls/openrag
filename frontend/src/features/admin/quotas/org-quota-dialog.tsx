import { type FormEvent, useState } from 'react';

import type { OrgQuotaOut } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogFooter } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Spinner } from '@/components/ui/spinner';
import { toast } from '@/components/ui/toaster';

import { useOrgQuota, useSetOrgQuota } from './queries';

function OrgQuotaForm({
  quota,
  onDone,
}: {
  quota: OrgQuotaOut | null;
  onDone: () => void;
}) {
  const save = useSetOrgQuota();
  const [monthly, setMonthly] = useState(String(quota?.monthly_tokens ?? 1_000_000));
  const [defaultUser, setDefaultUser] = useState(
    quota?.default_user_monthly_tokens == null
      ? ''
      : String(quota.default_user_monthly_tokens),
  );
  const [resetDay, setResetDay] = useState(String(quota?.reset_day ?? 1));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate(
      {
        monthly_tokens: Number(monthly),
        default_user_monthly_tokens: defaultUser === '' ? null : Number(defaultUser),
        reset_day: Number(resetDay),
      },
      {
        onSuccess: () => {
          toast.success('Organization token budget saved');
          onDone();
        },
        onError: (error) => toast.error(error.message),
      },
    );
  };

  return (
    <form onSubmit={submit} className="space-y-3">
      <div>
        <Label htmlFor="org-monthly-tokens">Organization monthly tokens</Label>
        <Input
          id="org-monthly-tokens"
          type="number"
          min={0}
          required
          value={monthly}
          onChange={(event) => setMonthly(event.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="org-default-user-tokens">Default monthly tokens per user</Label>
        <Input
          id="org-default-user-tokens"
          type="number"
          min={0}
          placeholder="No per-user limit"
          value={defaultUser}
          onChange={(event) => setDefaultUser(event.target.value)}
        />
      </div>
      <div>
        <Label htmlFor="org-reset-day">Monthly reset day</Label>
        <Input
          id="org-reset-day"
          type="number"
          min={1}
          max={31}
          required
          value={resetDay}
          onChange={(event) => setResetDay(event.target.value)}
        />
      </div>
      <p className="text-[11px] text-muted">
        Day 29–31 is automatically clamped to the last day of shorter months.
      </p>
      <DialogFooter>
        <Button type="button" onClick={onDone}>Cancel</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>Save budget</Button>
      </DialogFooter>
    </form>
  );
}

export function OrgQuotaDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const quota = useOrgQuota(open);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Organization token budget"
        description="Set the shared monthly ceiling and the default allocation inherited by users."
      >
        {quota.isPending ? <Spinner label="Loading token budget…" /> : null}
        {quota.isError ? <p role="alert" className="text-[12px] text-danger">{quota.error.message}</p> : null}
        {quota.isSuccess ? (
          <OrgQuotaForm quota={quota.data ?? null} onDone={() => onOpenChange(false)} />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
