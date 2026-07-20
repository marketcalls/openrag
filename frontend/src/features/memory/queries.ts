import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/api/problem';
import type { MemoryCreate, MemoryPatch, MemoryPreferencePatch } from '@/api/types';

const key = (workspaceId: string | null) => ['memories', workspaceId] as const;
const preferenceKey = (workspaceId: string | null) => ['memory-preferences', workspaceId] as const;

export function useMemories(workspaceId: string | null, includeHistory: boolean) {
  return useQuery({
    queryKey: [...key(workspaceId), includeHistory],
    enabled: workspaceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/memories', {
        params: {
          path: { workspace_id: workspaceId as string },
          query: { include_history: includeHistory, limit: 100 },
        },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load memories'));
      return data;
    },
  });
}

export function useMemoryPreferences(workspaceId: string | null) {
  return useQuery({
    queryKey: preferenceKey(workspaceId),
    enabled: workspaceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/workspaces/{workspace_id}/memories/preferences',
        { params: { path: { workspace_id: workspaceId as string } } },
      );
      if (error) throw new Error(problemDetail(error, 'Failed to load memory preferences'));
      return data;
    },
  });
}

export function useCreateMemory(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: MemoryCreate) => {
      if (!workspaceId) throw new Error('Select a workspace first');
      const { data, error } = await api.POST('/api/v1/workspaces/{workspace_id}/memories', {
        params: { path: { workspace_id: workspaceId } },
        body,
      });
      if (error) throw new Error(problemDetail(error, 'Failed to create memory'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: key(workspaceId) }),
  });
}

export function usePatchMemory(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { memoryId: string; body: MemoryPatch }) => {
      if (!workspaceId) throw new Error('Select a workspace first');
      const { data, error } = await api.PATCH(
        '/api/v1/workspaces/{workspace_id}/memories/{memory_id}',
        {
          params: {
            path: { workspace_id: workspaceId, memory_id: input.memoryId },
          },
          body: input.body,
        },
      );
      if (error) throw new Error(problemDetail(error, 'Failed to update memory'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: key(workspaceId) }),
  });
}

export function useForgetMemory(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (memoryId: string) => {
      if (!workspaceId) throw new Error('Select a workspace first');
      const { error } = await api.POST(
        '/api/v1/workspaces/{workspace_id}/memories/{memory_id}/forget',
        {
          params: { path: { workspace_id: workspaceId, memory_id: memoryId } },
          body: { client_request_id: crypto.randomUUID() },
        },
      );
      if (error) throw new Error(problemDetail(error, 'Failed to forget memory'));
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: key(workspaceId) }),
  });
}

export function usePatchMemoryPreferences(workspaceId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: MemoryPreferencePatch) => {
      if (!workspaceId) throw new Error('Select a workspace first');
      const { data, error } = await api.PATCH(
        '/api/v1/workspaces/{workspace_id}/memories/preferences',
        { params: { path: { workspace_id: workspaceId } }, body },
      );
      if (error) throw new Error(problemDetail(error, 'Failed to update memory preferences'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: preferenceKey(workspaceId) }),
  });
}

export async function exportMemories(workspaceId: string): Promise<void> {
  const { data, error } = await api.GET('/api/v1/workspaces/{workspace_id}/memories/export', {
    params: { path: { workspace_id: workspaceId } },
  });
  if (error) throw new Error(problemDetail(error, 'Failed to export memories'));
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
  );
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `openrag-memory-${workspaceId}.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}
