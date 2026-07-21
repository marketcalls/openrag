import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/api/problem';
import type { InvitationCreate } from '@/api/types';

export function useUsers() {
  return useQuery({
    queryKey: ['users'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/users');
      if (error) throw new Error(problemDetail(error, 'Failed to load users'));
      return data;
    },
  });
}

export function usePatchUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { userId: string; body: { active?: boolean } }) => {
      const { data, error } = await api.PATCH('/api/v1/users/{user_id}', {
        params: { path: { user_id: input.userId } },
        body: input.body,
      });
      if (error) throw new Error(problemDetail(error, 'Failed to update user'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  });
}

export function useDeleteUser() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (userId: string) => {
      const { error } = await api.DELETE('/api/v1/users/{user_id}', {
        params: { path: { user_id: userId } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to delete user'));
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  });
}

export function useInvite() {
  return useMutation({
    mutationFn: async (body: InvitationCreate) => {
      const { data, error } = await api.POST('/api/v1/auth/invitations', { body });
      if (error) throw new Error(problemDetail(error, 'Failed to create invitation'));
      return data;
    },
  });
}
