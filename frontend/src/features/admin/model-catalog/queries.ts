import { useQuery } from '@tanstack/react-query';

import { api } from '@/api/client';
import { problemDetail } from '@/api/problem';
import type { CatalogCapability } from '@/api/types';

export function useModelCatalog(capability: CatalogCapability, enabled: boolean) {
  return useQuery({
    queryKey: ['admin', 'model-catalog', capability],
    enabled,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const { data, error } = await api.GET('/api/v1/admin/model-catalog', {
        params: { query: { capability, limit: 1000 } },
      });
      if (error) throw new Error(problemDetail(error, 'Failed to load model catalog'));
      return data;
    },
  });
}
