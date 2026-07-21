import { lazy, Suspense } from 'react';
import { createBrowserRouter } from 'react-router-dom';

import { AppShell } from '@/components/layout/app-shell';
import { AcceptInvitePage } from '@/features/auth/accept-invite-page';
import { LoginPage } from '@/features/auth/login-page';
import { HomePage } from '@/features/landing/home-page';
import { Spinner } from '@/components/ui/spinner';

import { RequireAuth } from './require-auth';
import { RequirePermission, RequirePlatformSuperadmin } from './require-permission';
import { loadRouteModule } from './deployment-recovery';
import { RouteErrorPage } from './route-error-page';

const ChatPage = lazy(async () => {
  const module = await loadRouteModule(() => import('@/features/chat/chat-page'));
  return { default: module.ChatPage };
});
const DocumentsPage = lazy(async () => {
  const module = await loadRouteModule(() => import('@/features/documents/documents-page'));
  return { default: module.DocumentsPage };
});
const MemoryPage = lazy(async () => {
  const module = await loadRouteModule(() => import('@/features/memory/memory-page'));
  return { default: module.MemoryPage };
});
const UsersPage = lazy(async () => {
  const module = await loadRouteModule(() => import('@/features/admin/users/users-page'));
  return { default: module.UsersPage };
});
const ModelsPage = lazy(async () => {
  const module = await loadRouteModule(() => import('@/features/admin/models/models-page'));
  return { default: module.ModelsPage };
});
const EmbeddingProfilesPage = lazy(async () => {
  const module = await loadRouteModule(
    () => import('@/features/admin/embeddings/embedding-profiles-page'),
  );
  return { default: module.EmbeddingProfilesPage };
});
const RagOperationsPage = lazy(async () => {
  const module = await loadRouteModule(
    () => import('@/features/admin/rag-operations/rag-operations-page'),
  );
  return { default: module.RagOperationsPage };
});
const EvaluationsPage = lazy(async () => {
  const module = await loadRouteModule(
    () => import('@/features/admin/evaluations/evaluations-page'),
  );
  return { default: module.EvaluationsPage };
});
const RolesPage = lazy(async () => {
  const module = await loadRouteModule(() => import('@/features/admin/roles/roles-page'));
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

function MemoryRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Loading memory…" />
        </div>
      }
    >
      <MemoryPage />
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

function EmbeddingProfilesRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Loading embedding profiles…" />
        </div>
      }
    >
      <EmbeddingProfilesPage />
    </Suspense>
  );
}

function RagOperationsRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Loading RAG operations…" />
        </div>
      }
    >
      <RagOperationsPage />
    </Suspense>
  );
}

function EvaluationsRoute() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-1 items-center justify-center">
          <Spinner label="Loading RAG evaluations…" />
        </div>
      }
    >
      <EvaluationsPage />
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
    { path: '/', element: <HomePage /> },
    { path: '/login', element: <LoginPage /> },
    { path: '/invite', element: <AcceptInvitePage /> },
    {
      element: <RequireAuth />,
      errorElement: <RouteErrorPage />,
      children: [
        {
          element: <AppShell />,
          children: [
            { path: '/chat', element: <ChatRoute /> },
            { path: '/chat/:chatId', element: <ChatRoute /> },
            { path: '/documents', element: <DocumentsRoute /> },
            { path: '/memory', element: <MemoryRoute /> },
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
              children: [
                { path: '/admin/models', element: <ModelsRoute /> },
                {
                  path: '/admin/embedding-profiles',
                  element: <EmbeddingProfilesRoute />,
                },
                {
                  path: '/admin/rag-operations',
                  element: <RagOperationsRoute />,
                },
                {
                  path: '/admin/evaluations',
                  element: <EvaluationsRoute />,
                },
              ],
            },
          ],
        },
      ],
    },
  ],
  { future: { v7_relativeSplatPath: true } },
);
