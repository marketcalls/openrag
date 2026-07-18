import { useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

import { AuthCard } from './auth-card';
import { useAcceptInvite } from './mutations';

export function AcceptInvitePage() {
  const [parameters] = useSearchParams();
  const token = parameters.get('token');
  const accept = useAcceptInvite();
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [clientError, setClientError] = useState<string | null>(null);

  if (!token) {
    return (
      <AuthCard title="Accept invitation">
        <p className="text-[13px] text-secondary">
          This invitation link is invalid—it is missing its token. Ask your administrator for a
          new invitation.
        </p>
      </AuthCard>
    );
  }

  if (accept.isSuccess) {
    return (
      <AuthCard title="You're in">
        <p className="text-[13px] text-secondary">Your password is set.</p>
        <Button asChild variant="primary" className="mt-4 w-full">
          <Link to="/login">Go to sign in</Link>
        </Button>
      </AuthCard>
    );
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (password.length < 12) {
      setClientError('Password must be at least 12 characters.');
      return;
    }
    if (password !== confirmation) {
      setClientError('Passwords do not match.');
      return;
    }
    setClientError(null);
    accept.mutate({ token, password });
  };

  const error = clientError ?? (accept.isError ? accept.error.message : null);

  return (
    <AuthCard title="Set your password">
      <form onSubmit={onSubmit} className="space-y-3">
        <div>
          <Label htmlFor="invite-password">Password</Label>
          <Input
            id="invite-password"
            type="password"
            autoComplete="new-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="invite-confirmation">Confirm password</Label>
          <Input
            id="invite-confirmation"
            type="password"
            autoComplete="new-password"
            required
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
          />
        </div>
        {error ? (
          <p role="alert" className="text-[12px] text-danger">
            {error}
          </p>
        ) : null}
        <Button type="submit" variant="primary" className="w-full" disabled={accept.isPending}>
          {accept.isPending ? 'Setting password…' : 'Set password'}
        </Button>
      </form>
    </AuthCard>
  );
}
