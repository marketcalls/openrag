import { lazy, Suspense } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';
import { AcceptInvitePage } from '@/features/auth/accept-invite-page';
import { LoginPage } from '@/features/auth/login-page';
import { Spinner } from '@/components/ui/spinner';

import { RequireAuth } from './require-auth';
import { RequirePermission, RequirePlatformSuperadmin } from './require-permission';

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
const ModelsPage = lazy(async () => {
  const module = await import('@/features/admin/models/models-page');
  return { default: module.ModelsPage };
});
const RolesPage = lazy(async () => {
  const module = await import('@/features/admin/roles/roles-page');
  return { default: module.RolesPage };
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

function ModelsRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Loading models…" />
        </div>
      }
    >
      <ModelsPage />
    </Suspense>
  );
}

function RolesRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Loading roles…" />
        </div>
      }
    >
      <RolesPage />
    </Suspense>
  );
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
              element: <RequirePermission permission="user.manage" />,
              children: [{ path: '/admin/users', element: <UsersRoute /> }],
            },
            {
              element: <RequirePermission permission="role.manage" />,
              children: [{ path: '/admin/roles', element: <RolesRoute /> }],
            },
            {
              element: <RequirePlatformSuperadmin />,
              children: [{ path: '/admin/models', element: <ModelsRoute /> }],
            },
          ],
        },
      ],
    },
  ],
  { future: { v7_relativeSplatPath: true } },
);
