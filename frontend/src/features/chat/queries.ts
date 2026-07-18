import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';

export function useChats(workspaceId: string | null) {
  return useQuery({
    queryKey: ['chats'],
    enabled: workspaceId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/chats');
      if (error) throw new Error('Failed to load chats');
      return data;
    },
    select: (chats) => chats.filter((chat) => chat.workspace_id === workspaceId),
  });
}
