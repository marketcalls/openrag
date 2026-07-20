import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/api/problem';
import type { ModelCreate, ModelPatch } from '@/api/types';

function useInvalidateModels() {
  const queryClient = useQueryClient();

  return () => {
    void queryClient.invalidateQueries({ queryKey: ['admin-models'] });
    void queryClient.invalidateQueries({ queryKey: ['models'] });
  };
}

export function useAdminModels() {
  return useQuery({
    queryKey: ['admin-models'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/models');
      if (error) throw new Error('Failed to load models');
      return data;
    },
    refetchInterval: (query) =>
      query.state.data?.some((model) => model.probe_status === 'pending')
        ? 1000
        : false,
    refetchIntervalInBackground: false,
  });
}

export function useCreateModel() {
  const invalidate = useInvalidateModels();

  return useMutation({
    mutationFn: async (body: ModelCreate) => {
      const { data, error } = await api.POST('/api/v1/admin/models', { body });
      if (error) throw new Error(problemDetail(error, 'Failed to add model'));
      return data;
    },
    onSuccess: invalidate,
  });
}

export function usePatchModel() {
  const invalidate = useInvalidateModels();

  return useMutation({
    mutationFn: async (input: { modelId: string; body: ModelPatch }) => {
      const { data, error } = await api.PATCH('/api/v1/admin/models/{model_id}', {
        params: { path: { model_id: input.modelId } },
        body: input.body,
      });
      if (error) throw new Error(problemDetail(error, 'Failed to update model'));
      return data;
    },
    onSuccess: invalidate,
  });
}

export function useDeleteModel() {
  const invalidate = useInvalidateModels();

  return useMutation({
    mutationFn: async (modelId: string) => {
      const { error } = await api.DELETE('/api/v1/admin/models/{model_id}', {
        params: { path: { model_id: modelId } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to remove model'));
    },
    onSuccess: invalidate,
  });
}

export function useProbeModel() {
  const invalidate = useInvalidateModels();

  return useMutation({
    mutationFn: async (modelId: string) => {
      const { data, error } = await api.POST('/api/v1/admin/models/{model_id}/probe', {
        params: { path: { model_id: modelId } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to queue model probe'));
      return data;
    },
    onSuccess: invalidate,
  });
}
