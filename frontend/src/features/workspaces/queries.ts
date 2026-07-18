import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useWorkspaces() {
  return useQuery({
    queryKey: ['workspaces'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/workspaces');
      if (error) throw new Error('Failed to load workspaces');
      return data;
    },
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { name: string }) => {
      const { data, error } = await api.POST('/api/v1/workspaces', { body });
      if (error) throw new Error('Failed to create workspace');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}

export function useWorkspaceMembers(workspaceId: string | null) {
  return useQuery({
    queryKey: ['workspace-members', workspaceId],
    enabled: workspaceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET(
        '/api/v1/workspaces/{workspace_id}/members',
        { params: { path: { workspace_id: workspaceId as string } } },
      );
      if (error) throw new Error('Failed to load workspace members');
      return data;
    },
  });
}

export function useAddWorkspaceMember() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (input: { workspaceId: string; userId: string }) => {
      const { error } = await api.POST('/api/v1/workspaces/{workspace_id}/members', {
        params: { path: { workspace_id: input.workspaceId } },
        body: { user_id: input.userId, role: 'member' },
      });
      if (error) throw new Error('Failed to add workspace member');
    },
    onSuccess: (_data, input) =>
      void queryClient.invalidateQueries({
        queryKey: ['workspace-members', input.workspaceId],
      }),
  });
}

export function usePatchWorkspace() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (input: {
      workspaceId: string;
      defaultModelId: string | null;
    }) => {
      const { data, error } = await api.PATCH('/api/v1/workspaces/{workspace_id}', {
        params: { path: { workspace_id: input.workspaceId } },
        body: { default_model_id: input.defaultModelId },
      });
      if (error) throw new Error('Failed to update workspace');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['workspaces'] }),
  });
}
