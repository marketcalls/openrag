import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/api/problem';
import type { RagOperationsFilters } from '@/api/types';

const POLL_INTERVAL_MS = 30_000;

function visiblePollInterval() {
  return typeof document !== 'undefined' && document.visibilityState === 'visible'
    ? POLL_INTERVAL_MS
    : false;
}

function queryOptions() {
  return {
    staleTime: 15_000,
    refetchInterval: visiblePollInterval,
    refetchIntervalInBackground: false,
  } as const;
}

export function useRagOperationsOverview(filters: RagOperationsFilters) {
  return useQuery({
    queryKey: ['admin', 'rag-operations', 'overview', filters],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/rag-operations/overview', {
        params: { query: filters },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load RAG overview'));
      return data;
    },
    ...queryOptions(),
  });
}

export function useRagOperationsSeries(filters: RagOperationsFilters, interval: 'hour' | 'day') {
  return useQuery({
    queryKey: ['admin', 'rag-operations', 'series', interval, filters],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/rag-operations/series', {
        params: { query: { ...filters, interval } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load RAG time series'));
      return data;
    },
    ...queryOptions(),
  });
}

export function useRagOperationsRuns(filters: RagOperationsFilters) {
  return useQuery({
    queryKey: ['admin', 'rag-operations', 'runs', filters],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/rag-operations/runs', {
        params: { query: { ...filters, limit: 25 } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load recent RAG runs'));
      return data;
    },
    ...queryOptions(),
  });
}

export function useRagOperationsErrors(filters: RagOperationsFilters) {
  return useQuery({
    queryKey: ['admin', 'rag-operations', 'errors', filters],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/rag-operations/errors', {
        params: { query: { ...filters, limit: 25 } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load error groups'));
      return data;
    },
    ...queryOptions(),
  });
}

export function useRagRunDetail(runId: string | null, filters: RagOperationsFilters) {
  return useQuery({
    queryKey: ['admin', 'rag-operations', 'run', runId, filters],
    queryFn: async () => {
      if (!runId) throw new Error('Run id is required');
      const { data, error } = await api.GET('/api/v1/admin/rag-operations/runs/{run_id}', {
        params: { path: { run_id: runId }, query: filters },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load run detail'));
      return data;
    },
    enabled: Boolean(runId),
  });
}

export function useRagErrorDetail(issueId: string | null, filters: RagOperationsFilters) {
  return useQuery({
    queryKey: ['admin', 'rag-operations', 'error', issueId, filters],
    queryFn: async () => {
      if (!issueId) throw new Error('Issue id is required');
      const { data, error } = await api.GET('/api/v1/admin/rag-operations/errors/{issue_id}', {
        params: { path: { issue_id: issueId }, query: filters },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load error detail'));
      return data;
    },
    enabled: Boolean(issueId),
  });
}
