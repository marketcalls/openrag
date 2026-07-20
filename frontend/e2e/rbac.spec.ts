import { expect, test, type Page, type Route } from '@playwright/test';

const SUBJECT = '11111111-1111-4111-8111-111111111111';
const ORGANIZATION = '22222222-2222-4222-8222-222222222222';
const WORKSPACE_HSE = '33333333-3333-4333-8333-333333333333';
const WORKSPACE_FOREIGN = '44444444-4444-4444-8444-444444444444';

type Session = {
  permissions: string[];
  platformSuperadmin?: boolean;
  workspaces?: Array<{ id: string; name: string; default_model_id: null }>;
};

function encode(value: object): string {
  return Buffer.from(JSON.stringify(value)).toString('base64url');
}

function tokenFor(session: Session): string {
  return `${encode({ alg: 'HS256', typ: 'JWT' })}.${encode({
    sub: SUBJECT,
    org: ORGANIZATION,
    platform_superadmin: session.platformSuperadmin ?? false,
    permissions: session.permissions,
    exp: Math.floor(Date.now() / 1000) + 3_600,
  })}.test-signature`;
}

async function respond(route: Route, status: number, body: unknown): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function installApi(session: Session, page: Page): Promise<void> {
  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (!path.startsWith('/api/')) {
      await route.continue();
      return;
    }

    if (path === '/api/v1/auth/login' && request.method() === 'POST') {
      await respond(route, 200, { access_token: tokenFor(session), token_type: 'bearer' });
      return;
    }
    if (path === '/api/v1/auth/refresh') {
      await respond(route, 401, { detail: 'Not authenticated' });
      return;
    }
    if (path === '/api/v1/auth/logout') {
      await respond(route, 204, null);
      return;
    }
    if (path === '/api/v1/workspaces') {
      await respond(route, 200, session.workspaces ?? []);
      return;
    }
    if (path === '/api/v1/chats/search') {
      await respond(route, 200, { items: [], next_cursor: null });
      return;
    }
    if (path === '/api/v1/chats') {
      await respond(route, 200, []);
      return;
    }
    if (path === '/api/v1/users') {
      if (!session.permissions.includes('user.manage') && !session.platformSuperadmin) {
        await respond(route, 403, { detail: 'Missing permission: user.manage' });
        return;
      }
      await respond(route, 200, []);
      return;
    }
    if (path === '/api/v1/roles/catalog') {
      if (!session.permissions.includes('role.manage') && !session.platformSuperadmin) {
        await respond(route, 403, { detail: 'Missing permission: role.manage' });
        return;
      }
      await respond(route, 200, [
        {
          code: 'chat.use',
          label: 'Use chat',
          group: 'Chat',
          description: 'Ask grounded questions in authorized workspaces.',
        },
        {
          code: 'document.read',
          label: 'Read documents',
          group: 'Documents',
          description: 'Read authorized documents and citations.',
        },
      ]);
      return;
    }
    if (path === '/api/v1/roles') {
      if (!session.permissions.includes('role.manage') && !session.platformSuperadmin) {
        await respond(route, 403, { detail: 'Missing permission: role.manage' });
        return;
      }
      await respond(route, 200, [
        {
          id: '55555555-5555-4555-8555-555555555555',
          key: 'administrator',
          name: 'Administrator',
          description: 'Organization administration.',
          is_system: true,
          is_assignable: true,
          permissions: ['user.manage', 'role.manage'],
        },
      ]);
      return;
    }
    if (path === '/api/v1/admin/models') {
      if (!session.platformSuperadmin) {
        await respond(route, 403, { detail: 'Platform superadmin required' });
        return;
      }
      await respond(route, 200, []);
      return;
    }
    if (
      path.startsWith('/api/v1/admin/rag-operations')
      || path.startsWith('/api/v1/admin/evaluations')
    ) {
      if (!session.platformSuperadmin) {
        await respond(route, 403, { detail: 'Platform superadmin required' });
        return;
      }
      await respond(route, 200, []);
      return;
    }

    await respond(route, 200, []);
  });
}

async function login(page: Page, session: Session): Promise<void> {
  await installApi(session, page);
  await page.goto('/login');
  await page.getByLabel('Email').fill('browser-rbac@example.com');
  await page.getByLabel('Password').fill('not-a-real-credential');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page).toHaveURL(/\/chat$/);
}

test('Administrator sees organization administration but no platform controls', async ({
  page,
}) => {
  await login(page, {
    permissions: ['chat.use', 'user.manage', 'role.manage', 'workspace.manage'],
  });

  await expect(page.getByRole('link', { name: 'Users' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Roles' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Models' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Secrets' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'RAG operations' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Evaluations' })).toHaveCount(0);

  for (const path of ['/admin/rag-operations', '/admin/evaluations']) {
    await page.evaluate((nextPath) => {
      window.history.pushState({}, '', nextPath);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }, path);
    await expect(page).toHaveURL(/\/chat$/);
  }

  const denials = await page.evaluate(async () => {
    const responses = await Promise.all([
      fetch('/api/v1/admin/rag-operations/overview'),
      fetch('/api/v1/admin/evaluations/datasets'),
    ]);
    return Promise.all(responses.map(async (response) => ({
      status: response.status,
      body: await response.json(),
    })));
  });
  expect(denials).toEqual([
    { status: 403, body: { detail: 'Platform superadmin required' } },
    { status: 403, body: { detail: 'Platform superadmin required' } },
  ]);
});

test('organization role editor cannot select platform superadmin', async ({ page }) => {
  await login(page, { permissions: ['chat.use', 'role.manage'] });
  await page.getByRole('link', { name: 'Roles' }).click();
  await expect(page.getByRole('table', { name: 'Organization roles' })).toBeVisible();
  await page.getByRole('button', { name: 'Create role' }).click();

  await expect(page.getByRole('dialog', { name: 'Create a custom role' })).toBeVisible();
  await expect(page.getByText(/platform superadmin/i)).toHaveCount(0);
  await expect(page.getByRole('checkbox', { name: /platform/i })).toHaveCount(0);
});

test('Engineer cannot open organization administration and the API denies it', async ({
  page,
}) => {
  await login(page, { permissions: ['chat.use', 'document.read'] });

  await expect(page.getByRole('link', { name: 'Users' })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Roles' })).toHaveCount(0);
  await page.evaluate(() => {
    window.history.pushState({}, '', '/admin/users');
    window.dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page).toHaveURL(/\/chat$/);

  const denial = await page.evaluate(async () => {
    const response = await fetch('/api/v1/users');
    return { status: response.status, body: await response.json() };
  });
  expect(denial).toEqual({
    status: 403,
    body: { detail: 'Missing permission: user.manage' },
  });
});

test('HSE Manager only sees the workspace returned by its authorized scope', async ({ page }) => {
  await login(page, {
    permissions: ['chat.use', 'document.read', 'document.upload'],
    workspaces: [
      { id: WORKSPACE_HSE, name: 'HSE Approved', default_model_id: null },
    ],
  });

  await page.getByRole('button', { name: 'Switch workspace' }).click();
  await expect(page.getByRole('menuitem', { name: 'HSE Approved' })).toBeVisible();
  await expect(page.getByText(WORKSPACE_FOREIGN)).toHaveCount(0);
  await expect(page.getByRole('menuitem', { name: /Foreign workspace/i })).toHaveCount(0);
});

test('platform superadmin retains platform administration', async ({ page }) => {
  await login(page, { permissions: [], platformSuperadmin: true });

  await expect(page.getByRole('link', { name: 'Models' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'RAG operations' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Evaluations' })).toBeVisible();
  await page.getByRole('link', { name: 'Models' }).click();
  await expect(page).toHaveURL(/\/admin\/models$/);
  await expect(page.getByRole('table', { name: 'Model registry' })).toBeVisible();
});
