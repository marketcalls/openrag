import { decodeClaims, hasPermission } from './jwt';

function fakeJwt(payload: object): string {
  const base64 = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${base64({ alg: 'HS256' })}.${base64(payload)}.sig`;
}

const USER_ID = '550e8400-e29b-41d4-a716-446655440000';
const ORG_ID = '6ba7b810-9dad-41d1-80b4-00c04fd430c8';
const FUTURE_EXPIRY = 4_102_444_800;

test('decodes capability claims used only as UI hints', () => {
  const claims = decodeClaims(
    fakeJwt({
      sub: USER_ID,
      org: ORG_ID,
      platform_superadmin: false,
      permissions: ['document.read', 'role.manage'],
      exp: FUTURE_EXPIRY,
    }),
  );
  expect(claims).toEqual({
    sub: USER_ID,
    org: ORG_ID,
    platform_superadmin: false,
    permissions: ['document.read', 'role.manage'],
    exp: FUTURE_EXPIRY,
  });
});

test('returns null for garbage', () => {
  expect(decodeClaims('not-a-jwt')).toBeNull();
  expect(decodeClaims('a.%%%.c')).toBeNull();
});

test('rejects every malformed permission array even for platform claims', () => {
  for (const permissions of [undefined, 'role.manage', ['role.manage', 7]]) {
    const claims = decodeClaims(
      fakeJwt({
        sub: USER_ID,
        org: ORG_ID,
        platform_superadmin: true,
        permissions,
        exp: FUTURE_EXPIRY,
      }),
    );
    expect(claims).toBeNull();
  }
});

test('does not treat a legacy role name as a permission', () => {
  const claims = decodeClaims(
    fakeJwt({
      sub: USER_ID,
      org: ORG_ID,
      role: 'admin',
      platform_superadmin: false,
      permissions: [],
      exp: FUTURE_EXPIRY,
    }),
  );
  expect(claims).not.toBeNull();
  expect(hasPermission(claims!, 'role.manage')).toBe(false);
});

test('rejects malformed identity, platform, and expiry claims', () => {
  const valid = {
    sub: USER_ID,
    org: ORG_ID,
    platform_superadmin: false,
    permissions: [],
    exp: FUTURE_EXPIRY,
  };
  expect(decodeClaims(fakeJwt({ ...valid, sub: 'u1' }))).toBeNull();
  expect(decodeClaims(fakeJwt({ ...valid, org: 'o1' }))).toBeNull();
  expect(decodeClaims(fakeJwt({ ...valid, platform_superadmin: 1 }))).toBeNull();
  expect(decodeClaims(fakeJwt({ ...valid, exp: true }))).toBeNull();
  expect(decodeClaims(fakeJwt({ ...valid, exp: 1 }))).toBeNull();
});

test('platform superadmin has every UI permission without organization claims', () => {
  const claims = decodeClaims(
    fakeJwt({
      sub: USER_ID,
      org: ORG_ID,
      platform_superadmin: true,
      permissions: [],
      exp: FUTURE_EXPIRY,
    }),
  );
  expect(hasPermission(claims!, 'model.configure')).toBe(true);
});
