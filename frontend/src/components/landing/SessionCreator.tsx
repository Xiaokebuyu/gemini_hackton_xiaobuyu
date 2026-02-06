/**
 * Session creator form - world selection and session creation
 */
import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Loader2 } from 'lucide-react';
import { createSession, listRecoverableSessions } from '../../api';
import type { RecoverableSessionItem } from '../../types';

interface SessionCreatorProps {
  onSessionCreated: (worldId: string, sessionId: string) => void;
}

const PRESET_WORLDS = [
  { id: 'goblin_slayer', label: 'Goblin Slayer', description: 'A dark fantasy world of monster hunting' },
  { id: 'tavern_tales', label: 'Tavern Tales', description: 'Lighthearted pub adventures' },
];

export const SessionCreator: React.FC<SessionCreatorProps> = ({ onSessionCreated }) => {
  const [worldId, setWorldId] = useState('goblin_slayer');
  const [userId, setUserId] = useState('player-001');
  const [isCreating, setIsCreating] = useState(false);
  const [isLoadingSessions, setIsLoadingSessions] = useState(false);
  const [recoverableSessions, setRecoverableSessions] = useState<RecoverableSessionItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const handleCreate = async () => {
    if (!worldId.trim() || !userId.trim()) return;

    setIsCreating(true);
    setError(null);

    try {
      const response = await createSession(worldId, {
        user_id: userId,
      });
      onSessionCreated(response.world_id, response.session_id);
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
        bg-sketch-bg-panel
        border border-sketch-ink-secondary
        rounded-xl
        shadow-parchment-lg
        p-6
      "
    >
      {/* World selection */}
      <label className="block mb-4">
        <span className="text-sm font-fantasy text-sketch-ink-secondary mb-2 block">
          World
        </span>
        <div className="space-y-2">
          {PRESET_WORLDS.map((world) => (
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
                    ? 'border-sketch-accent-gold bg-sketch-accent-gold/10 shadow-parchment-sm'
                    : 'border-sketch-ink-faint bg-sketch-bg-input hover:border-sketch-ink-muted'
                }
              `}
            >
              <div className="font-body text-sm font-medium text-sketch-ink-primary">
                {world.label}
              </div>
              <div className="font-body text-xs text-sketch-ink-muted mt-0.5">
                {world.description}
              </div>
            </motion.button>
          ))}
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
            bg-sketch-bg-input
            text-sketch-ink-primary
            border border-sketch-ink-muted
            rounded-lg
            focus:border-sketch-accent-gold
            focus:outline-none
            transition-colors
          "
          placeholder="Or enter custom world_id..."
        />
      </label>

      {/* User ID */}
      <label className="block mb-5">
        <span className="text-sm font-fantasy text-sketch-ink-secondary mb-2 block">
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
            bg-sketch-bg-input
            text-sketch-ink-primary
            border border-sketch-ink-muted
            rounded-lg
            focus:border-sketch-accent-gold
            focus:outline-none
            transition-colors
          "
          placeholder="Your player ID"
        />
      </label>

      {/* Error */}
      {error && (
        <p className="text-xs text-sketch-accent-red mb-3">{error}</p>
      )}

      {/* Recoverable sessions */}
      <div className="mb-4">
        <button
          onClick={handleLoadSessions}
          disabled={isLoadingSessions || !worldId.trim() || !userId.trim()}
          className="
            w-full mb-2
            px-3 py-2
            bg-sketch-bg-input
            text-sketch-ink-primary
            border border-sketch-ink-muted
            rounded-lg
            text-sm font-body
            hover:border-sketch-accent-gold
            disabled:opacity-50 disabled:cursor-not-allowed
            transition-colors
          "
        >
          {isLoadingSessions ? 'Loading sessions...' : 'Load Recoverable Sessions'}
        </button>

        {recoverableSessions.length > 0 && (
          <div className="max-h-36 overflow-y-auto space-y-2 sketch-scrollbar">
            {recoverableSessions.map((session) => (
              <button
                key={session.session_id}
                onClick={() => onSessionCreated(session.world_id, session.session_id)}
                className="
                  w-full text-left
                  p-2 rounded-lg
                  bg-sketch-bg-input
                  border border-sketch-ink-faint
                  hover:border-sketch-accent-gold
                  transition-colors
                "
              >
                <div className="text-xs font-body text-sketch-ink-primary truncate">
                  {session.session_id}
                </div>
                <div className="text-[11px] font-body text-sketch-ink-muted mt-0.5">
                  {session.player_location || '未知地点'} · {formatTime(session.updated_at)}
                </div>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Submit */}
      <motion.button
        whileHover={isCreating ? {} : { scale: 1.02, y: -1 }}
        whileTap={isCreating ? {} : { scale: 0.98 }}
        onClick={handleCreate}
        disabled={isCreating || !worldId.trim() || !userId.trim()}
        className="
          w-full
          px-4 py-3
          bg-gradient-to-b from-[#d4ad2e] to-[#c9a227]
          text-sketch-ink-primary
          font-fantasy text-base
          rounded-lg
          border border-sketch-accent-gold
          shadow-parchment-sm
          hover:shadow-parchment-glow-gold
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
    </div>
  );
};

export default SessionCreator;
