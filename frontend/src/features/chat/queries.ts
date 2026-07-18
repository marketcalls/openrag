import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

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

export function useChat(chatId: string | null) {
  return useQuery({
    queryKey: ['chat', chatId],
    enabled: chatId !== null,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/chats/{chat_id}', {
        params: { path: { chat_id: chatId as string } },
      });
      if (error) throw new Error('Failed to load chat');
      return data;
    },
  });
}

export function useCreateChat() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: { workspace_id: string }) => {
      const { data, error } = await api.POST('/api/v1/chats', { body });
      if (error) throw new Error('Failed to create chat');
      return data;
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['chats'] }),
  });
}
