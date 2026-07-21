import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/api/problem';

export function useOrgQuota(enabled = true) {
  return useQuery({
    queryKey: ['usage', 'org-quota'],
    enabled,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/usage/org/quota');
      if (error) throw new Error(problemDetail(error, 'Failed to load organization quota'));
      return data;
    },
  });
}

export function useSetOrgQuota() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (body: {
      monthly_tokens: number;
      default_user_monthly_tokens: number | null;
      reset_day: number;
    }) => {
      const { data, error } = await api.PUT('/api/v1/usage/org/quota', { body });
      if (error) throw new Error(problemDetail(error, 'Failed to save organization quota'));
      return data;
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['usage'] });
    },
  });
}

export function useUserQuota(userId: string, enabled: boolean) {
  return useQuery({
    queryKey: ['usage', 'user-quota', userId],
    enabled: enabled && userId !== '',
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/users/{user_id}/quota', {
        params: { path: { user_id: userId } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load user quota'));
      return data;
    },
  });
}

export function useSetUserQuota() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      userId,
      monthlyTokens,
    }: {
      userId: string;
      monthlyTokens: number | null;
    }) => {
      const { error } = await api.PUT('/api/v1/users/{user_id}/quota', {
        params: { path: { user_id: userId } },
        body: { monthly_tokens: monthlyTokens },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to save user quota'));
    },
    onSuccess: (_, { userId }) => {
      void client.invalidateQueries({ queryKey: ['usage', 'user-quota', userId] });
      void client.invalidateQueries({ queryKey: ['usage', 'me'] });
    },
  });
}
