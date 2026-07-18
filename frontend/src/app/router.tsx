import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';
import { AcceptInvitePage } from '@/features/auth/accept-invite-page';
import { LoginPage } from '@/features/auth/login-page';
import { Spinner } from '@/components/ui/spinner';

import { RequireAuth } from './require-auth';
import { RequireRole } from './require-role';

const ChatPage = lazy(async () => {
  const module = await import('@/features/chat/chat-page');
  return { default: module.ChatPage };
});
const DocumentsPage = lazy(async () => {
  const module = await import('@/features/documents/documents-page');
  return { default: module.DocumentsPage };
});
const UsersPage = lazy(async () => {
  const module = await import('@/features/admin/users/users-page');
  return { default: module.UsersPage };
});

function ChatRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Loading chat…" />
        </div>
      }
    >
      <ChatPage />
    </Suspense>
  );
}

function DocumentsRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Loading documents…" />
        </div>
      }
    >
      <DocumentsPage />
    </Suspense>
  );
}

function UsersRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Loading users…" />
        </div>
      }
    >
      <UsersPage />
    </Suspense>
  );
}

function ComingSoon({ name }: { name: string }) {
  return <p className="p-6 text-secondary">{name} — under construction</p>;
}

export const router = createBrowserRouter(
  [
    { path: '/login', element: <LoginPage /> },
    { path: '/invite', element: <AcceptInvitePage /> },
    {
      element: <RequireAuth />,
      children: [
        {
          element: <AppShell />,
          children: [
            { path: '/', element: <Navigate to="/chat" replace /> },
            { path: '/chat', element: <ChatRoute /> },
            { path: '/chat/:chatId', element: <ChatRoute /> },
            { path: '/documents', element: <DocumentsRoute /> },
            {
              element: <RequireRole role="admin" />,
              children: [{ path: '/admin/users', element: <UsersRoute /> }],
            },
            {
              element: <RequireRole role="superadmin" />,
              children: [{ path: '/admin/models', element: <ComingSoon name="Models" /> }],
            },
          ],
        },
      ],
    },
  ],
  { future: { v7_relativeSplatPath: true } },
);
