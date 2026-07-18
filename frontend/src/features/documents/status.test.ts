import type { DocumentOut } from '@/api/types';

import { formatBytes, shouldPoll, statusPresentation } from './status';

test.each([
  ['indexed', 'success', 'Indexed'],
  ['queued', 'accent', 'Processing'],
  ['processing', 'accent', 'Processing'],
  ['failed', 'danger', 'Failed'],
] as const)('%s maps to a %s status pill labeled %s', (status, tone, label) => {
  expect(statusPresentation({ status })).toEqual({ tone, label });
});

test('polls only while at least one document is in flight', () => {
  const document = (status: string) => ({ status }) as DocumentOut;
  expect(shouldPoll(undefined)).toBe(false);
  expect(shouldPoll([document('indexed'), document('failed')])).toBe(false);
  expect(shouldPoll([document('indexed'), document('processing')])).toBe(true);
  expect(shouldPoll([document('queued')])).toBe(true);
});

test('formats byte sizes for compact table display', () => {
  expect(formatBytes(512)).toBe('512 B');
  expect(formatBytes(2048)).toBe('2.0 KB');
  expect(formatBytes(10_485_760)).toBe('10.0 MB');
});
