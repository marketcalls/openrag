import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import type { ModelPublic } from '@/api/types';

const MODEL_DISCOVERY_INTERVAL_MS = 1_000;

export function modelRefetchInterval(models: ModelPublic[] | undefined): number | false {
  return models?.length ? false : MODEL_DISCOVERY_INTERVAL_MS;
}

export function useModels() {
  return useQuery({
    queryKey: ['models'],
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/models');
      if (error) throw new Error('Failed to load models');
      return data;
    },
    refetchInterval: (query) => modelRefetchInterval(query.state.data),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  });
}
