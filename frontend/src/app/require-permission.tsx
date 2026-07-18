import { Navigate, Outlet } from 'react-router-dom';

import { hasPermission } from '@/lib/jwt';
import { useClaims } from '@/lib/use-claims';

export function RequirePermission({ permission }: { permission: string }) {
  const claims = useClaims();
  return claims && hasPermission(claims, permission) ? (
    <Outlet />
  ) : (
    <Navigate to="/chat" replace />
  );
}

export function RequirePlatformSuperadmin() {
  const claims = useClaims();
  return claims?.platform_superadmin ? <Outlet /> : <Navigate to="/chat" replace />;
}
