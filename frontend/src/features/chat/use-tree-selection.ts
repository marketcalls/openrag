import { useCallback, useMemo, useState } from 'react';

import type { MessageOut } from '@/api/types';

import { selectActivePath, type PathEntry } from './tree';

export function useTreeSelection(messages: readonly MessageOut[] | undefined): {
  path: PathEntry[];
  select: (branchKey: string, id: string) => void;
} {
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const path = useMemo(
    () => selectActivePath(messages ?? [], overrides),
    [messages, overrides],
  );
  const select = useCallback((branchKey: string, id: string) => {
    setOverrides((current) => ({ ...current, [branchKey]: id }));
  }, []);
  return { path, select };
}
