/**
 * Session creator form - world selection and session creation
 */
import React, { useState, useEffect } from 'react';
import { motion } from 'framer-motion';
import { Loader2 } from 'lucide-react';
import { createSession, listRecoverableSessions, listWorlds } from '../../api';
import type { RecoverableSessionItem, CreateGameSessionResponse } from '../../types';

interface SessionCreatorProps {
  onSessionCreated: (worldId: string, sessionId: string, createResponse?: CreateGameSessionResponse) => void;
}

interface WorldInfo {
  id: string;
  name: string;
  description: string;
}

export const SessionCreator: React.FC<SessionCreatorProps> = ({ onSessionCreated }) => {
  const [worldId, setWorldId] = useState('');
  const [userId, setUserId] = useState('player-001');
  const [isCreating, setIsCreating] = useState(false);
  const [isLoadingSessions, setIsLoadingSessions] = useState(false);
  const [isLoadingWorlds, setIsLoadingWorlds] = useState(true);
  const [worlds, setWorlds] = useState<WorldInfo[]>([]);
  const [recoverableSessions, setRecoverableSessions] = useState<RecoverableSessionItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  // Load available worlds on mount
  useEffect(() => {
    let cancelled = false;
    const loadWorlds = async () => {
      setIsLoadingWorlds(true);
      try {
        const response = await listWorlds();
        if (!cancelled) {
          setWorlds(response.worlds);
          if (response.worlds.length > 0) {
            setWorldId((current) => current || response.worlds[0].id);
          }
        }
      } catch (err) {
        console.error('Failed to load worlds:', err);
        if (!cancelled) {
          setError('Failed to load worlds. Is the backend running?');
        }
      } finally {
        if (!cancelled) {
          setIsLoadingWorlds(false);
        }
      }
    };
    loadWorlds();
    return () => { cancelled = true; };
  }, []);

  const handleCreate = async () => {
    if (!worldId.trim() || !userId.trim()) return;

    setIsCreating(true);
    setError(null);

    try {
      const response = await createSession(worldId, {
        user_id: userId,
      });
      onSessionCreated(response.world_id, response.session_id, response);
    } catch (err) {
      console.error('Session creation failed:', err);
      const detail =
        typeof err === 'object' &&
        err !== null &&
        'response' in err &&
        typeof (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ===
          'string'
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : null;
      setError(detail || 'Failed to create session. Please check world_id and backend status.');
    } finally {
      setIsCreating(false);
    }
  };

  const handleLoadSessions = async () => {
    if (!worldId.trim() || !userId.trim()) return;

    setIsLoadingSessions(true);
    setError(null);
    setRecoverableSessions([]);

    try {
      const response = await listRecoverableSessions(worldId.trim(), userId.trim(), 20);
      setRecoverableSessions(response.sessions);
      if (response.sessions.length === 0) {
        setError('No recoverable sessions found for this world and user.');
      }
    } catch (err) {
      console.error('List sessions failed:', err);
      const detail =
        typeof err === 'object' &&
        err !== null &&
        'response' in err &&
        typeof (err as { response?: { data?: { detail?: unknown } } }).response?.data?.detail ===
          'string'
          ? (err as { response?: { data?: { detail?: string } } }).response?.data?.detail
          : null;
      setError(detail || 'Failed to load recoverable sessions.');
    } finally {
      setIsLoadingSessions(false);
    }
  };

  const formatTime = (raw: string): string => {
    const time = new Date(raw);
    if (Number.isNaN(time.getTime())) {
      return raw;
    }
    return time.toLocaleString();
  };

  return (
    <div
      className="
        w-[360px]
        bg-g-bg-surface
        border border-g-border-strong
        rounded-xl
        shadow-g-lg
        p-6
      "
    >
      {/* World selection */}
      <label className="block mb-4">
        <span className="text-sm font-heading text-g-text-secondary mb-2 block">
          World
        </span>
        <div className="space-y-2">
          {isLoadingWorlds ? (
            <div className="flex items-center justify-center py-4 text-g-text-muted text-sm">
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
              Loading worlds...
            </div>
          ) : worlds.length > 0 ? (
            worlds.map((world) => (
              <motion.button
                key={world.id}
                whileHover={{ scale: 1.01 }}
                whileTap={{ scale: 0.99 }}
                onClick={() => setWorldId(world.id)}
                className={`
                  w-full text-left
                  p-3 rounded-lg border
                  transition-all duration-200
                  ${
                    worldId === world.id
                      ? 'border-g-gold bg-g-bg-hover shadow-g-sm'
                      : 'border-g-border bg-g-bg-input hover:border-g-border-strong'
                  }
                `}
              >
                <div className="font-body text-sm font-medium text-g-text-primary">
                  {world.name}
                </div>
                {world.description && (
                  <div className="font-body text-xs text-g-text-muted mt-0.5">
                    {world.description.length > 80
                      ? world.description.slice(0, 80) + '...'
                      : world.description}
                  </div>
                )}
              </motion.button>
            ))
          ) : (
            <div className="text-xs text-g-red py-2">
              No worlds available in Firestore.
            </div>
          )}
        </div>
        {/* Custom world input */}
        <input
          type="text"
          value={worldId}
          onChange={(e) => setWorldId(e.target.value)}
          className="
            mt-2 w-full
            px-3 py-2
            font-body text-sm
            bg-g-bg-input
            text-g-text-primary
            border border-g-border-strong
            rounded-lg
            focus:border-g-gold
            focus:outline-none
            transition-colors
          "
          placeholder="Or enter custom world_id..."
        />
      </label>

      {/* User ID */}
      <label className="block mb-5">
        <span className="text-sm font-heading text-g-text-secondary mb-2 block">
          Player ID
        </span>
        <input
          type="text"
          value={userId}
          onChange={(e) => setUserId(e.target.value)}
          className="
            w-full
            px-3 py-2
            font-body text-sm
            bg-g-bg-input
            text-g-text-primary
            border border-g-border-strong
            rounded-lg
            focus:border-g-gold
            focus:outline-none
            transition-colors
          "
          placeholder="Your player ID"
        />
      </label>

      {/* Error */}
      {error && (
        <p className="text-xs text-g-red mb-3">{error}</p>
      )}

      {/* Submit - primary action */}
      <motion.button
        whileHover={isCreating ? {} : { scale: 1.02, y: -1 }}
        whileTap={isCreating ? {} : { scale: 0.98 }}
        onClick={handleCreate}
        disabled={isCreating || !worldId.trim() || !userId.trim()}
        className="
          w-full mb-4
          px-4 py-3
          bg-g-gold hover:bg-g-gold-dark
          text-white
          font-heading text-base
          rounded-lg
          border border-g-gold
          shadow-g-sm
          hover:shadow-g-gold
          disabled:opacity-50 disabled:cursor-not-allowed
          transition-all duration-200
          flex items-center justify-center gap-2
        "
      >
        {isCreating ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Creating...</span>
          </>
        ) : (
          <span>Enter World</span>
        )}
      </motion.button>

      {/* Recoverable sessions - secondary action */}
      <div>
        <button
          onClick={handleLoadSessions}
          disabled={isLoadingSessions || !worldId.trim() || !userId.trim()}
          className="
            w-full mb-2
            px-3 py-2
            bg-g-bg-input
            text-g-text-primary
            border border-g-border-strong
            rounded-lg
            text-sm font-body
            hover:border-g-gold
            disabled:opacity-50 disabled:cursor-not-allowed
            transition-colors
          "
        >
          {isLoadingSessions ? 'Loading sessions...' : 'Load Recoverable Sessions'}
        </button>

        {recoverableSessions.length > 0 && (
          <div className="max-h-36 overflow-y-auto space-y-2 g-scrollbar">
            {recoverableSessions.map((session) => (
              <button
                key={session.session_id}
                onClick={() => onSessionCreated(session.world_id, session.session_id)}
                className="
                  w-full text-left
                  p-2 rounded-lg
                  bg-g-bg-input
                  border border-g-border
                  hover:border-g-gold
                  transition-colors
                "
              >
                <div className="text-xs font-body text-g-text-primary truncate">
                  {session.session_id}
                </div>
                <div className="text-[11px] font-body text-g-text-muted mt-0.5">
                  {session.player_location || 'Unknown location'} · {formatTime(session.updated_at)}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

export default SessionCreator;
