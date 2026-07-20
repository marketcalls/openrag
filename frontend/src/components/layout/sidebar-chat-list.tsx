import { MessageSquarePlus, Search, Trash2 } from 'lucide-react';
import { useDeferredValue, useState } from 'react';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { toast } from 'sonner';

import { useChatSearch, useDeleteChat } from '@/features/chat/queries';
import { useWorkspace } from '@/features/workspaces/workspace-context';
import { cn } from '@/lib/cn';

import { Button } from '../ui/button';

export function SidebarChatList() {
  const { workspaceId } = useWorkspace();
  const [query, setQuery] = useState('');
  const deferredQuery = useDeferredValue(query);
  const chatsQuery = useChatSearch(workspaceId, deferredQuery);
  const chats = chatsQuery.data?.pages.flatMap((page) => page.items) ?? [];
  const deleteChat = useDeleteChat();
  const navigate = useNavigate();
  const location = useLocation();

  const remove = (chatId: string, title: string) => {
    if (!window.confirm(`Delete “${title || 'Untitled chat'}”? This cannot be undone.`)) return;
    deleteChat.mutate(chatId, {
      onSuccess: () => {
        toast.success('Chat deleted');
        if (location.pathname === `/chat/${chatId}`) navigate('/chat', { replace: true });
      },
      onError: (error) => toast.error(error.message),
    });
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center justify-between px-2 pb-1">
        <span className="text-[11px] font-medium uppercase tracking-wide text-muted">Chats</span>
        <Button variant="ghost" size="icon" aria-label="New chat" onClick={() => navigate('/chat')}>
          <MessageSquarePlus className="h-4 w-4" aria-hidden />
        </Button>
      </div>
      <div className="relative px-2 pb-2">
        <Search
          className="pointer-events-none absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted"
          aria-hidden
        />
        <input
          type="search"
          aria-label="Search chats"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search chats"
          className="h-8 w-full rounded-md border border-line bg-surface pl-8 pr-2 text-[12px] text-ink outline-none placeholder:text-muted focus:border-accent focus:ring-2 focus:ring-accent/15"
        />
      </div>
      <nav aria-label="Chats" className="min-h-0 flex-1 space-y-0.5 overflow-y-auto px-1">
        {chats.map((chat) => (
          <div key={chat.id} className="group flex items-center gap-0.5 rounded-md">
            <NavLink
              to={`/chat/${chat.id}`}
              className={({ isActive }) =>
                cn(
                  'min-w-0 flex-1 truncate rounded-md px-2 py-1.5 text-[13px] text-secondary hover:bg-subtle hover:text-ink focus:outline-none focus:ring-2 focus:ring-accent/20',
                  isActive && 'bg-subtle text-ink',
                )
              }
            >
              {chat.title || 'Untitled chat'}
            </NavLink>
            <Button
              variant="ghost"
              size="icon"
              aria-label={`Delete ${chat.title || 'chat'}`}
              disabled={deleteChat.isPending}
              onClick={() => remove(chat.id, chat.title)}
              className="h-7 w-7 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100 motion-reduce:transition-none"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden />
            </Button>
          </div>
        ))}
        {!chatsQuery.isPending && chats.length === 0 ? (
          <p className="px-2 py-3 text-[12px] text-muted">
            {query.trim() ? 'No matching chats' : 'No chats yet'}
          </p>
        ) : null}
        {chatsQuery.hasNextPage ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full text-[12px]"
            disabled={chatsQuery.isFetchingNextPage}
            onClick={() => void chatsQuery.fetchNextPage()}
          >
            {chatsQuery.isFetchingNextPage ? 'Loading…' : 'Load older chats'}
          </Button>
        ) : null}
      </nav>
    </div>
  );
}
