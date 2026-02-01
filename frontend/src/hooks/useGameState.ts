/**
 * Combined game state hook
 */
import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useGameStore, useChatStore } from '../stores';
import { getGameState, getParty, getLocation } from '../api';

export function useGameState() {
  const { worldId, sessionId, setParty, setLocation, setGameTime, updateFromGameState } =
    useGameStore();
  const { addSystemMessage } = useChatStore();

  // Fetch game state
  const gameStateQuery = useQuery({
    queryKey: ['gameState', worldId, sessionId],
    queryFn: async () => {
      if (!worldId || !sessionId) throw new Error('No session');
      return getGameState(worldId, sessionId);
    },
    enabled: !!worldId && !!sessionId,
    staleTime: 30000,
  });

  // Fetch party
  const partyQuery = useQuery({
    queryKey: ['party', worldId, sessionId],
    queryFn: async () => {
      if (!worldId || !sessionId) throw new Error('No session');
      return getParty(worldId, sessionId);
    },
    enabled: !!worldId && !!sessionId,
    staleTime: 60000,
  });

  // Fetch location
  const locationQuery = useQuery({
    queryKey: ['location', worldId, sessionId],
    queryFn: async () => {
      if (!worldId || !sessionId) throw new Error('No session');
      return getLocation(worldId, sessionId);
    },
    enabled: !!worldId && !!sessionId,
    staleTime: 30000,
  });

  // Update stores when data changes
  useEffect(() => {
    if (gameStateQuery.data) {
      updateFromGameState(gameStateQuery.data);
    }
  }, [gameStateQuery.data, updateFromGameState]);

  useEffect(() => {
    if (partyQuery.data) {
      setParty(partyQuery.data);
    }
  }, [partyQuery.data, setParty]);

  useEffect(() => {
    if (locationQuery.data) {
      setLocation(locationQuery.data);
      if (locationQuery.data.time) {
        setGameTime(locationQuery.data.time);
      }
    }
  }, [locationQuery.data, setLocation, setGameTime]);

  // Handle errors
  useEffect(() => {
    if (gameStateQuery.error) {
      addSystemMessage(`Error loading game state: ${gameStateQuery.error.message}`);
    }
  }, [gameStateQuery.error, addSystemMessage]);

  return {
    isLoading:
      gameStateQuery.isLoading || partyQuery.isLoading || locationQuery.isLoading,
    error: gameStateQuery.error || partyQuery.error || locationQuery.error,
    refetch: () => {
      gameStateQuery.refetch();
      partyQuery.refetch();
      locationQuery.refetch();
    },
  };
}

export default useGameState;
