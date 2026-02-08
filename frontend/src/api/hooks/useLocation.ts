/**
 * Location query hook
 */
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getLocation, getGameTime } from '../gameApi';
import { useGameStore } from '../../stores/gameStore';
import type { LocationResponse, GameTimeResponse } from '../../types';

export function useLocation() {
  const { worldId, sessionId, setLocation } = useGameStore();

  const locationQuery = useQuery<LocationResponse>({
    queryKey: ['location', worldId, sessionId],
    queryFn: () => {
      if (!worldId || !sessionId) {
        throw new Error('No active session');
      }
      return getLocation(worldId, sessionId);
    },
    enabled: !!worldId && !!sessionId,
    staleTime: 30000, // 30 seconds
    refetchOnWindowFocus: false,
  });

  // Sync location data to gameStore so store-dependent components stay updated
  useEffect(() => {
    if (locationQuery.data) {
      setLocation(locationQuery.data);
    }
  }, [locationQuery.data, setLocation]);

  return {
    location: locationQuery.data,
    gameTime: locationQuery.data?.time,
    isLoading: locationQuery.isLoading,
    error: locationQuery.error,
    refetch: locationQuery.refetch,
  };
}

export function useGameTime() {
  const { worldId, sessionId } = useGameStore();

  const timeQuery = useQuery<GameTimeResponse>({
    queryKey: ['gameTime', worldId, sessionId],
    queryFn: () => {
      if (!worldId || !sessionId) {
        throw new Error('No active session');
      }
      return getGameTime(worldId, sessionId);
    },
    enabled: !!worldId && !!sessionId,
    staleTime: 60000, // 1 minute
    refetchInterval: 60000, // Refetch every minute
  });

  return {
    gameTime: timeQuery.data,
    isLoading: timeQuery.isLoading,
    error: timeQuery.error,
  };
}
