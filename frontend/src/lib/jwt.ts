export interface Claims {
  sub: string;
  org: string;
  platform_superadmin: boolean;
  permissions: string[];
  exp: number;
}

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Payload decode only: claims are UI hints; the server remains authoritative. */
export function decodeClaims(token: string): Claims | null {
  const part = token.split('.')[1];
  if (!part) return null;

  try {
    const normalized = part.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
    const payload = JSON.parse(atob(padded)) as Record<string, unknown>;
    if (
      typeof payload.sub !== 'string' ||
      !UUID_PATTERN.test(payload.sub) ||
      typeof payload.org !== 'string' ||
      !UUID_PATTERN.test(payload.org) ||
      typeof payload.platform_superadmin !== 'boolean' ||
      typeof payload.exp !== 'number' ||
      !Number.isInteger(payload.exp) ||
      payload.exp <= Date.now() / 1000
    ) {
      return null;
    }

    const permissions =
      Array.isArray(payload.permissions) &&
      payload.permissions.every((permission) => typeof permission === 'string')
        ? payload.permissions
        : [];

    return {
      sub: payload.sub,
      org: payload.org,
      platform_superadmin: payload.platform_superadmin,
      permissions,
      exp: payload.exp,
    };
  } catch {
    return null;
  }
}

export function hasPermission(claims: Claims, permission: string): boolean {
  return claims.platform_superadmin || claims.permissions.includes(permission);
}
