import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { api, authFetch } from '@/api/client';
import type { ChatOut } from '@/api/types';

interface ChatSearchPage {
  items: ChatOut[];
  next_cursor: string | null;
}

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

export function useChatSearch(workspaceId: string | null, query: string) {
  return useInfiniteQuery({
    queryKey: ['chat-search', workspaceId, query],
    enabled: workspaceId !== null,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }) => {
      const parameters = new URLSearchParams({
        workspace_id: workspaceId as string,
        limit: '50',
      });
      if (query.trim()) parameters.set('q', query.trim());
      if (pageParam) parameters.set('cursor', pageParam);
      const response = await authFetch(
        new Request(
          new URL(`/api/v1/chats/search?${parameters.toString()}`, window.location.origin),
          { method: 'GET', credentials: 'include' },
        ),
      );
      if (!response.ok) throw new Error('Failed to search chats');
      return (await response.json()) as ChatSearchPage;
    },
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

export function useDeleteChat() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (chatId: string) => {
      const { error } = await api.DELETE('/api/v1/chats/{chat_id}', {
        params: { path: { chat_id: chatId } },
      });
      if (error) throw new Error('Failed to delete chat');
    },
    onSuccess: async (_value, chatId) => {
      await queryClient.invalidateQueries({ queryKey: ['chats'] });
      await queryClient.invalidateQueries({ queryKey: ['chat-search'] });
      queryClient.removeQueries({ queryKey: ['chat', chatId] });
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
