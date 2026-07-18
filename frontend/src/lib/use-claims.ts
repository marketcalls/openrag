import { useSyncExternalStore } from 'react';

import { getAccessToken, subscribeAuth } from './auth-store';
import { decodeClaims, type Claims } from './jwt';

export function useClaims(): Claims | null {
  const token = useSyncExternalStore(subscribeAuth, getAccessToken, getAccessToken);
  return token ? decodeClaims(token) : null;
}
