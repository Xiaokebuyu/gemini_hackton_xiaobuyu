/**
 * Party query hook
 */
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getParty } from '../gameApi';
import { useGameStore } from '../../stores/gameStore';
import type { Party } from '../../types';

export function useParty() {
  const { worldId, sessionId, setParty } = useGameStore();

  const partyQuery = useQuery<Party | null>({
    queryKey: ['party', worldId, sessionId],
    queryFn: async () => {
      if (!worldId || !sessionId) throw new Error('No active session');
      return getParty(worldId, sessionId);
    },
    enabled: !!worldId && !!sessionId,
    staleTime: 60000,
    refetchOnWindowFocus: false,
  });

  useEffect(() => {
    if (partyQuery.data !== undefined) {
      setParty(partyQuery.data);
    }
  }, [partyQuery.data, setParty]);

  return {
    party: partyQuery.data,
    isLoading: partyQuery.isLoading,
    error: partyQuery.error,
    refetch: partyQuery.refetch,
  };
}
