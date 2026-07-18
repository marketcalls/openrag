import { Navigate, Outlet } from 'react-router-dom';

import { useClaims } from '@/lib/use-claims';

export function RequireRole({ role }: { role: 'admin' | 'superadmin' }) {
  const claims = useClaims();
  const allowed =
    claims !== null &&
    (claims.role === 'superadmin' || (role === 'admin' && claims.role === 'admin'));
  return allowed ? <Outlet /> : <Navigate to="/chat" replace />;
}
