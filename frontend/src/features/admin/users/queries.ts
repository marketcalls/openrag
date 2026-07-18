import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';

export type ManagedRole = 'admin' | 'user';

export function useUsers() {
  return useQuery({
    queryKey: ['users'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/users');
      if (error) throw new Error('Failed to load users');
      return data;
    },
  });
}

export function usePatchUser() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (input: {
      userId: string;
      body: { active?: boolean; role?: ManagedRole };
    }) => {
      const { data, error } = await api.PATCH('/api/v1/users/{user_id}', {
        params: { path: { user_id: input.userId } },
        body: input.body,
      });
      if (error) throw new Error('Failed to update user');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  });
}

export function useInvite() {
  return useMutation({
    mutationFn: async (body: { email: string; role: ManagedRole }) => {
      const { data, error } = await api.POST('/api/v1/auth/invitations', { body });
      if (error) throw new Error('Failed to create invitation');
      return data;
    },
  });
}
