import { useEffect, useState } from 'react';
import { Navigate, Outlet, useNavigate } from 'react-router-dom';

import { refreshAccessToken, setOnAuthFailure } from '@/api/client';
import { Spinner } from '@/components/ui/spinner';
import { getAccessToken } from '@/lib/auth-store';

type Gate = 'checking' | 'authed' | 'anonymous';

export function RequireAuth() {
  const navigate = useNavigate();
  const [gate, setGate] = useState<Gate>(() => (getAccessToken() ? 'authed' : 'checking'));

  // This one-shot session bootstrap is not ongoing server state: an httpOnly
  // refresh cookie restores the in-memory access token after a page reload.
  useEffect(() => {
    if (gate !== 'checking') return;
    let cancelled = false;
    void refreshAccessToken().then((authenticated) => {
      if (!cancelled) setGate(authenticated ? 'authed' : 'anonymous');
    });
    return () => {
      cancelled = true;
    };
  }, [gate]);

  useEffect(() => {
    setOnAuthFailure(() => navigate('/login', { replace: true }));
    return () => setOnAuthFailure(() => undefined);
  }, [navigate]);

  if (gate === 'checking') {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Spinner label="Signing you in…" />
      </div>
    );
  }
  if (gate === 'anonymous') return <Navigate to="/login" replace />;
  return <Outlet />;
}
