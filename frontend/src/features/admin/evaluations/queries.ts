import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/api/problem';
import type {
  EvaluationDatasetCreate,
  EvaluationDatasetVersionCreate,
  EvaluationRunCreate,
} from '@/api/types';

const ACTIVE_RUN_POLL_MS = 2_000;

export function useEvaluationDatasets(workspaceId: string | null) {
  return useQuery({
    queryKey: ['admin', 'evaluations', 'datasets', workspaceId],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/evaluations/datasets', {
        params: { query: { workspace_id: workspaceId } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load evaluation datasets'));
      return data;
    },
    enabled: Boolean(workspaceId),
  });
}

export function useCreateEvaluationDataset() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: EvaluationDatasetCreate) => {
      const { data, error } = await api.POST('/api/v1/admin/evaluations/datasets', { body });
      if (error) throw new Error(problemDetail(error, 'Failed to create evaluation dataset'));
      return data;
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['admin', 'evaluations', 'datasets'] }),
  });
}

export function useEvaluationVersions(datasetId: string | null) {
  return useQuery({
    queryKey: ['admin', 'evaluations', 'versions', datasetId],
    queryFn: async () => {
      if (!datasetId) throw new Error('Dataset id is required');
      const { data, error } = await api.GET('/api/v1/admin/evaluations/datasets/{dataset_id}/versions', {
        params: { path: { dataset_id: datasetId } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load dataset versions'));
      return data;
    },
    enabled: Boolean(datasetId),
  });
}

export function useCreateEvaluationVersion(datasetId: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: EvaluationDatasetVersionCreate) => {
      if (!datasetId) throw new Error('Dataset id is required');
      const { data, error } = await api.POST('/api/v1/admin/evaluations/datasets/{dataset_id}/versions', {
        params: { path: { dataset_id: datasetId } }, body,
      });
      if (error) throw new Error(problemDetail(error, 'Failed to seal dataset version'));
      return data;
    },
    onSuccess: () => void client.invalidateQueries({ queryKey: ['admin', 'evaluations', 'versions', datasetId] }),
  });
}

export function useEvaluationRuns(datasetVersionId: string | null) {
  return useQuery({
    queryKey: ['admin', 'evaluations', 'runs', datasetVersionId],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/evaluations/runs', {
        params: { query: { dataset_version_id: datasetVersionId } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load evaluation runs'));
      return data;
    },
    enabled: Boolean(datasetVersionId),
    refetchInterval: (query) => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return false;
      const runs = query.state.data;
      return runs?.some((run) => run.status === 'queued' || run.status === 'running')
        ? ACTIVE_RUN_POLL_MS : false;
    },
    refetchIntervalInBackground: false,
  });
}

export function useEvaluationRun(runId: string | null) {
  return useQuery({
    queryKey: ['admin', 'evaluations', 'run', runId],
    queryFn: async () => {
      if (!runId) throw new Error('Run id is required');
      const { data, error } = await api.GET('/api/v1/admin/evaluations/runs/{run_id}', {
        params: { path: { run_id: runId } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load evaluation run'));
      return data;
    },
    enabled: Boolean(runId),
  });
}

export function useCreateEvaluationRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: EvaluationRunCreate) => {
      const { data, error } = await api.POST('/api/v1/admin/evaluations/runs', { body });
      if (error) throw new Error(problemDetail(error, 'Failed to queue evaluation'));
      return data;
    },
    onSuccess: (run) => {
      client.setQueryData(['admin', 'evaluations', 'run', run.id], run);
      void client.invalidateQueries({ queryKey: ['admin', 'evaluations', 'runs'] });
    },
  });
}
