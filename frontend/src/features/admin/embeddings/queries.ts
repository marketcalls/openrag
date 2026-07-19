import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import type { EmbeddingProfileCreate, EmbeddingProfilePatch } from '@/api/types';
import { api } from '@/api/client';

const queryKey = ['admin', 'embedding-profiles'] as const;

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
