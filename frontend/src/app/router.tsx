import { createBrowserRouter, Navigate, Outlet } from 'react-router-dom';

import { AcceptInvitePage } from '@/features/auth/accept-invite-page';
import { LoginPage } from '@/features/auth/login-page';

import { RequireAuth } from './require-auth';

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
          element: <Outlet />,
          children: [
            { path: '/', element: <Navigate to="/chat" replace /> },
            { path: '/chat', element: <ComingSoon name="Chat" /> },
            { path: '/chat/:chatId', element: <ComingSoon name="Chat" /> },
            { path: '/documents', element: <ComingSoon name="Documents" /> },
            { path: '/admin/users', element: <ComingSoon name="Users" /> },
            { path: '/admin/models', element: <ComingSoon name="Models" /> },
          ],
        },
      ],
    },
  ],
  { future: { v7_relativeSplatPath: true } },
);
