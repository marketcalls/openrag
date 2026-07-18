import createClient from 'openapi-fetch';

import { getAccessToken, setAccessToken } from '@/lib/auth-store';

import type { paths } from './schema';

let onAuthFailure: () => void = () => undefined;

export function setOnAuthFailure(callback: () => void): void {
  onAuthFailure = callback;
}

let refreshInFlight: Promise<boolean> | null = null;

export function refreshAccessToken(): Promise<boolean> {
  refreshInFlight ??= doRefresh().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

async function doRefresh(): Promise<boolean> {
  const response = await fetch('/api/v1/auth/refresh', {
    method: 'POST',
    credentials: 'include',
  });
  if (!response.ok) {
    setAccessToken(null);
    return false;
  }

  const body = (await response.json()) as { access_token: string };
  setAccessToken(body.access_token);
  return true;
}

const NO_REFRESH = [
  '/api/v1/auth/login',
  '/api/v1/auth/refresh',
  '/api/v1/auth/invitations/accept',
];

export async function authFetch(input: Request): Promise<Response> {
  const send = (): Promise<Response> => {
    const request = input.clone();
    const token = getAccessToken();
    if (token) request.headers.set('Authorization', `Bearer ${token}`);
    return fetch(request);
  };

  let response = await send();
  if (response.status === 401 && !NO_REFRESH.some((path) => input.url.includes(path))) {
    if (await refreshAccessToken()) {
      response = await send();
    } else {
      onAuthFailure();
    }
  }
  return response;
}

export const api = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: 'include',
  fetch: authFetch,
});
