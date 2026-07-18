import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/api/problem';
import type { RoleBindingReplace, RoleCreate, RolePatch } from '@/api/types';

export const roleKeys = {
  all: ['roles'] as const,
  catalog: ['roles', 'catalog'] as const,
};

export function useRoles() {
  return useQuery({
    queryKey: roleKeys.all,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/roles');
      if (error) throw new Error(problemDetail(error, 'Failed to load roles'));
      return data;
    },
  });
}

export function usePermissionCatalog(enabled = true) {
  return useQuery({
    queryKey: roleKeys.catalog,
    enabled,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/roles/catalog');
      if (error) throw new Error(problemDetail(error, 'Failed to load permission catalog'));
      return data;
    },
  });
}

export function useCreateRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: RoleCreate) => {
      const { data, error } = await api.POST('/api/v1/roles', { body });
      if (error) throw new Error(problemDetail(error, 'Failed to create role'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: roleKeys.all }),
  });
}

export function usePatchRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ roleId, body }: { roleId: string; body: RolePatch }) => {
      const { data, error } = await api.PATCH('/api/v1/roles/{role_id}', {
        params: { path: { role_id: roleId } },
        body,
      });
      if (error) throw new Error(problemDetail(error, 'Failed to update role'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: roleKeys.all }),
  });
}

export function useDeleteRole() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (roleId: string) => {
      const { error } = await api.DELETE('/api/v1/roles/{role_id}', {
        params: { path: { role_id: roleId } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to delete role'));
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: roleKeys.all }),
  });
}

export function useReplaceRoleBindings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ userId, body }: { userId: string; body: RoleBindingReplace }) => {
      const { data, error } = await api.PUT('/api/v1/users/{user_id}/role-bindings', {
        params: { path: { user_id: userId } },
        body,
      });
      if (error) throw new Error(problemDetail(error, 'Failed to replace role bindings'));
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['users'] }),
  });
}
