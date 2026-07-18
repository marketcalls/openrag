import { decodeClaims } from './jwt';

function fakeJwt(payload: object): string {
  const base64 = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `${base64({ alg: 'HS256' })}.${base64(payload)}.sig`;
}

test('decodes sub, org, role, and exp', () => {
  const claims = decodeClaims(fakeJwt({ sub: 'u1', org: 'o1', role: 'admin', exp: 123 }));
  expect(claims).toEqual({ sub: 'u1', org: 'o1', role: 'admin', exp: 123 });
});

test('returns null for garbage', () => {
  expect(decodeClaims('not-a-jwt')).toBeNull();
  expect(decodeClaims('a.%%%.c')).toBeNull();
});

test('rejects claims with an unsupported role', () => {
  expect(decodeClaims(fakeJwt({ sub: 'u1', org: 'o1', role: 'owner', exp: 123 }))).toBeNull();
});
