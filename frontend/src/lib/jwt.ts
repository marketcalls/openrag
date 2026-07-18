export interface AccessClaims {
  sub: string;
  org: string;
  role: 'superadmin' | 'admin' | 'user';
  exp: number;
}

const ROLES = new Set<AccessClaims['role']>(['superadmin', 'admin', 'user']);

/** Payload decode only: this is for UI hints; the server remains authoritative. */
export function decodeClaims(token: string): AccessClaims | null {
  const part = token.split('.')[1];
  if (!part) return null;

  try {
    const normalized = part.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    const payload = JSON.parse(atob(padded)) as Record<string, unknown>;
    if (
      typeof payload.sub !== 'string' ||
      typeof payload.org !== 'string' ||
      typeof payload.role !== 'string' ||
      !ROLES.has(payload.role as AccessClaims['role']) ||
      typeof payload.exp !== 'number'
    ) {
      return null;
    }

    return {
      sub: payload.sub,
      org: payload.org,
      role: payload.role as AccessClaims['role'],
      exp: payload.exp,
    };
  } catch {
    return null;
  }
}
