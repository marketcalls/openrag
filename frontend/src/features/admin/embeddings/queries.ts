import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { problemDetail } from '@/api/problem';
import type {
  EmbeddingDeploymentCreate,
  EmbeddingProfileCreate,
  EmbeddingProfilePatch,
} from '@/api/types';
import { api } from '@/api/client';

const queryKey = ['admin', 'embedding-profiles'] as const;
const deploymentQueryKey = ['admin', 'embedding-deployments'] as const;

export function useEmbeddingProfiles() {
  return useQuery({
    queryKey,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/embedding-profiles');
      if (error) throw new Error('Failed to load embedding profiles');
      return data;
    },
  });
}

export function useCreateEmbeddingProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: EmbeddingProfileCreate) => {
      const { data, error } = await api.POST('/api/v1/admin/embedding-profiles', {
        body,
      });
      if (error) throw new Error('Failed to create embedding profile');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey }),
  });
}

export function usePatchEmbeddingProfile() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      profileId: string;
      body: EmbeddingProfilePatch;
    }) => {
      const { data, error } = await api.PATCH(
        '/api/v1/admin/embedding-profiles/{profile_id}',
        {
          params: { path: { profile_id: input.profileId } },
          body: input.body,
        },
      );
      if (error) throw new Error('Failed to update embedding profile');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey }),
  });
}

export function useEmbeddingDeployments() {
  return useQuery({
    queryKey: deploymentQueryKey,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/embedding-deployments');
      if (error) throw new Error(problemDetail(error, 'Failed to load embedding deployments'));
      return data;
    },
    refetchInterval: (query) =>
      query.state.data?.some((deployment) => deployment.status === 'building') ? 2000 : false,
  });
}

export function useRequestEmbeddingDeployment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: EmbeddingDeploymentCreate) => {
      const { data, error } = await api.POST('/api/v1/admin/embedding-deployments', {
        body,
      });
      if (error) throw new Error(problemDetail(error, 'Failed to start embedding deployment'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: deploymentQueryKey }),
  });
}

export function useActivateEmbeddingDeployment() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (deploymentId: string) => {
      const { data, error } = await api.POST(
        '/api/v1/admin/embedding-deployments/{deployment_id}/activate',
        { params: { path: { deployment_id: deploymentId } } },
      );
      if (error) throw new Error(problemDetail(error, 'Failed to activate embedding deployment'));
      return data;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: deploymentQueryKey });
      void queryClient.invalidateQueries({ queryKey });
    },
  });
}
