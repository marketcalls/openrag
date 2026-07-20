import { describe, expect, test, vi } from 'vitest';

import { recoverStaleChunk } from './deployment-recovery';

class MemoryStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string) {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string) {
    this.values.set(key, value);
  }
}

describe('stale deployment chunk recovery', () => {
  test('reloads once for a missing dynamic import', () => {
    const storage = new MemoryStorage();
    const reload = vi.fn();
    const error = new TypeError(
      'Failed to fetch dynamically imported module: /assets/chat-page-old.js',
    );

    expect(recoverStaleChunk(error, { storage, reload })).toBe(true);
    expect(recoverStaleChunk(error, { storage, reload })).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  test('does not reload for an unrelated application exception', () => {
    const reload = vi.fn();

    expect(
      recoverStaleChunk(new Error('query failed'), {
        storage: new MemoryStorage(),
        reload,
      }),
    ).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});
