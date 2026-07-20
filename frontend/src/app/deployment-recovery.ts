const RECOVERY_KEY = 'openrag:stale-chunk-recovery:v1';
const STALE_CHUNK_PATTERNS = [
  /failed to fetch dynamically imported module/i,
  /importing a module script failed/i,
  /chunkloaderror/i,
  /loading chunk .+ failed/i,
];

interface RecoveryStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface RecoveryEnvironment {
  storage: RecoveryStorage;
  reload: () => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function browserEnvironment(): RecoveryEnvironment {
  return {
    storage: window.sessionStorage,
    reload: () => window.location.reload(),
  };
}

export function recoverStaleChunk(
  error: unknown,
  environment: RecoveryEnvironment = browserEnvironment(),
): boolean {
  const message = errorMessage(error);
  if (!STALE_CHUNK_PATTERNS.some((pattern) => pattern.test(message))) return false;

  // The exact failed asset URL is part of the fingerprint. A second failure
  // for the same deployment is allowed to reach the branded route boundary
  // instead of entering an infinite reload loop.
  const fingerprint = message.slice(0, 1_024);
  try {
    if (environment.storage.getItem(RECOVERY_KEY) === fingerprint) return false;
    environment.storage.setItem(RECOVERY_KEY, fingerprint);
  } catch {
    return false;
  }
  environment.reload();
  return true;
}

export async function loadRouteModule<T>(loader: () => Promise<T>): Promise<T> {
  try {
    return await loader();
  } catch (error) {
    if (recoverStaleChunk(error)) {
      // Navigation replaces this document. Keeping Suspense pending prevents a
      // transient raw router error from flashing before the reload begins.
      return await new Promise<T>(() => undefined);
    }
    throw error;
  }
}
