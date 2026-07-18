import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { useClaims } from '@/lib/use-claims';

import { useWorkspaces } from './queries';

interface WorkspaceState {
  workspaceId: string | null;
  setWorkspaceId: (id: string) => void;
}

const WorkspaceContext = createContext<WorkspaceState | null>(null);

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const claims = useClaims();
  const storageKey = `openrag-workspace:${claims?.sub ?? 'anonymous'}`;
  const { data: workspaces } = useWorkspaces();
  const [workspaceId, setWorkspaceIdState] = useState<string | null>(() =>
    localStorage.getItem(storageKey),
  );

  useEffect(() => {
    if (!workspaces) return;
    if (workspaces.length === 0) {
      setWorkspaceIdState(null);
      return;
    }
    if (!workspaceId || !workspaces.some((workspace) => workspace.id === workspaceId)) {
      const firstId = workspaces[0]?.id ?? null;
      setWorkspaceIdState(firstId);
      if (firstId) localStorage.setItem(storageKey, firstId);
    }
  }, [storageKey, workspaceId, workspaces]);

  const value = useMemo<WorkspaceState>(
    () => ({
      workspaceId,
      setWorkspaceId: (id: string) => {
        localStorage.setItem(storageKey, id);
        setWorkspaceIdState(id);
      },
    }),
    [storageKey, workspaceId],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceState {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error('useWorkspace must be used inside WorkspaceProvider');
  return context;
}
