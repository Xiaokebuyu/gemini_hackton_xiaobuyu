/**
 * Session creator - dual-mode world selection and session creation/resume
 */
import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Loader2, ChevronDown, Lock } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { createSession, listRecoverableSessions } from '../../api';
import type { RecoverableSessionItem, CreateGameSessionResponse, CharacterCreationResponse } from '../../types';
import { CharacterCreation } from '../character/CharacterCreation';

interface SessionCreatorProps {
  mode: 'new' | 'continue';
  onSessionCreated: (worldId: string, sessionId: string, createResponse?: CreateGameSessionResponse) => void;
}

const FIXED_WORLD_ID = 'final-world';

function formatRelativeTime(raw: string): string {
  const time = new Date(raw);
  if (Number.isNaN(time.getTime())) return raw;
  const diffMs = Date.now() - time.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return 'Just now';
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  return time.toLocaleDateString();
}

export const SessionCreator: React.FC<SessionCreatorProps> = ({ mode, onSessionCreated }) => {
  const { t } = useTranslation();
  const worldId = FIXED_WORLD_ID;
  const [userId, setUserId] = useState('player-001');
  const [isCreating, setIsCreating] = useState(false);
  const [isLoadingSessions, setIsLoadingSessions] = useState(false);
  const [recoverableSessions, setRecoverableSessions] = useState<RecoverableSessionItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [creationState, setCreationState] = useState<{
    worldId: string;
    sessionId: string;
    location: any;
    time: any;
  } | null>(null);

  // Auto-load sessions when world changes in continue mode
  useEffect(() => {
    if (mode !== 'continue' || !worldId.trim() || !userId.trim()) return;
    let cancelled = false;

    const loadSessions = async () => {
      setIsLoadingSessions(true);
      setError(null);
      setRecoverableSessions([]);
      try {
        const response = await listRecoverableSessions(worldId.trim(), userId.trim(), 20);
        if (!cancelled) {
          setRecoverableSessions(response.sessions);
          if (response.sessions.length === 0) {
            setError(t('landing.noSessions'));
          }
        }
      } catch (err) {
        console.error('List sessions failed:', err);
        if (!cancelled) {
          setError('Failed to load sessions.');
        }
      } finally {
        if (!cancelled) {
          setIsLoadingSessions(false);
        }
      }
    };

    loadSessions();
    return () => { cancelled = true; };
  }, [mode, worldId, userId, t]);

  const handleCreate = async () => {
    const effectiveWorldId = worldId.trim();
    if (!effectiveWorldId || !userId.trim()) return;

    setIsCreating(true);
    setError(null);

    try {
      const response = await createSession(effectiveWorldId, {
        user_id: userId,
      });
      if (response.phase === 'character_creation') {
        setCreationState({
          worldId: response.world_id,
          sessionId: response.session_id,
          location: response.location,
          time: response.time,
        });
      } else {
        onSessionCreated(response.world_id, response.session_id, response);
      }
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

  const handleCharacterCreated = (charResponse: CharacterCreationResponse) => {
    const fullResponse = {
      session_id: creationState!.sessionId,
      world_id: creationState!.worldId,
      location: creationState!.location,
      time: creationState!.time,
      opening_narration: charResponse.opening_narration,
      phase: 'active' as const,
    };
    onSessionCreated(fullResponse.world_id, fullResponse.session_id, fullResponse as CreateGameSessionResponse);
  };

  // If in character creation flow, show the creation UI
  if (creationState) {
    return (
      <div
        className="
          w-full
          bg-[var(--g-title-bg-surface)]
          backdrop-blur-md
          border border-[var(--g-title-border)]
          border-t-2 border-t-[var(--g-accent-gold)]
          rounded-xl
          shadow-g-title-glow
          cursor-default
        "
      >
        <CharacterCreation
          worldId={creationState.worldId}
          sessionId={creationState.sessionId}
          onComplete={handleCharacterCreated}
        />
      </div>
    );
  }

  const panelTitle = mode === 'new' ? t('landing.chooseWorld') : t('landing.resumeJourney');

  return (
    <div
      className="
        w-full
        bg-[var(--g-title-bg-surface)]
        backdrop-blur-md
        border border-[var(--g-title-border)]
        border-t-2 border-t-[var(--g-accent-gold)]
        rounded-xl
        shadow-g-title-glow
        p-6
        cursor-default
      "
    >
      {/* Panel title */}
      <h2 className="font-heading text-lg text-[var(--g-accent-gold)] mb-5 text-center flex items-center justify-center gap-3">
        <span className="h-px w-8 bg-gradient-to-r from-transparent to-[var(--g-accent-gold)]/60" />
        <span className="text-xs opacity-60">✦</span>
        <span>{panelTitle}</span>
        <span className="text-xs opacity-60">✦</span>
        <span className="h-px w-8 bg-gradient-to-l from-transparent to-[var(--g-accent-gold)]/60" />
      </h2>

      {/* World selection */}
      <div className="mb-5">
        <div
          className="
            flex items-center justify-between gap-3
            p-4 rounded-lg border
            border-[var(--g-title-border-strong)]
            bg-[rgba(196,154,42,0.08)]
            shadow-g-card-glow
          "
        >
          <div className="min-w-0">
            <div className="font-heading text-sm text-[var(--g-title-text-primary)]">
              final-world
            </div>
            <div className="font-body text-xs text-[var(--g-title-text-muted)] mt-1 leading-relaxed">
              环境已锁定为生产世界，无法切换
            </div>
          </div>
          <Lock className="w-4 h-4 text-[var(--g-accent-gold)] shrink-0" />
        </div>
      </div>

      {/* Error */}
      {error && (
        <p className="text-xs text-g-red mb-3">{error}</p>
      )}

      {/* New Adventure: Enter World button */}
      {mode === 'new' && (
        <motion.button
          whileHover={isCreating ? {} : { scale: 1.02, y: -1 }}
          whileTap={isCreating ? {} : { scale: 0.98 }}
          onClick={handleCreate}
          disabled={isCreating || !worldId.trim() || !userId.trim()}
          className="
            w-full mb-4
            px-4 py-3
            bg-[var(--g-accent-gold)] hover:bg-[var(--g-accent-gold-dark)]
            text-white
            font-heading text-base
            rounded-lg
            border border-[var(--g-accent-gold)]
            shadow-g-gold
            disabled:opacity-50 disabled:cursor-not-allowed
            transition-all duration-200
            flex items-center justify-center gap-2
          "
        >
          {isCreating ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>{t('landing.creating')}</span>
            </>
          ) : (
            <span>{t('landing.enterWorld')}</span>
          )}
        </motion.button>
      )}

      {/* Continue Journey: Session list */}
      {mode === 'continue' && (
        <div className="mb-4">
          {isLoadingSessions ? (
            <div className="flex items-center justify-center py-4 text-[var(--g-title-text-muted)] text-sm">
              <Loader2 className="w-4 h-4 animate-spin mr-2" />
              {t('landing.searchingSessions')}
            </div>
          ) : recoverableSessions.length > 0 ? (
            <div className="space-y-2 max-h-48 overflow-y-auto g-scrollbar">
              <div className="text-xs font-body text-[var(--g-title-text-muted)] mb-2 uppercase tracking-wider">
                {t('landing.savedSessions')}
              </div>
              {recoverableSessions.map((session, index) => (
                <motion.button
                  key={session.session_id}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: index * 0.05, duration: 0.25 }}
                  onClick={() => {
                    if (session.needs_character_creation) {
                      setCreationState({
                        worldId: session.world_id,
                        sessionId: session.session_id,
                        location: null,
                        time: null,
                      });
                    } else {
                      onSessionCreated(session.world_id, session.session_id);
                    }
                  }}
                  className="
                    w-full text-left
                    flex items-start gap-3
                    p-3 rounded-lg
                    bg-[rgba(26,21,16,0.6)]
                    border border-[var(--g-title-border)]
                    hover:border-[var(--g-accent-gold)]
                    hover:shadow-g-card-glow
                    transition-all duration-200
                  "
                >
                  {/* Gold left indicator bar */}
                  <div className="w-1 self-stretch rounded-full shrink-0 bg-[var(--g-title-border)] group-hover:bg-[var(--g-accent-gold)] transition-colors duration-200" />
                  <div className="flex-1 flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-body text-sm text-[var(--g-title-text-primary)] truncate">
                        {session.player_location || t('landing.unknownLocation')}
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        {session.chapter_id && (
                          <span className="
                            text-[10px] font-body px-1.5 py-0.5 rounded
                            bg-[rgba(196,154,42,0.15)] text-[var(--g-accent-gold)]
                          ">
                            {session.chapter_id}
                          </span>
                        )}
                        {session.sub_location && (
                          <span className="text-[11px] font-body text-[var(--g-title-text-muted)]">
                            {session.sub_location}
                          </span>
                        )}
                      </div>
                    </div>
                    <span className="text-[11px] font-body text-[var(--g-title-text-muted)] shrink-0">
                      {formatRelativeTime(session.updated_at)}
                    </span>
                  </div>
                </motion.button>
              ))}
            </div>
          ) : null}
        </div>
      )}

      {/* Advanced Options */}
      <div className="border-t border-[var(--g-title-border)] pt-3">
        <button
          onClick={() => setShowAdvanced(!showAdvanced)}
          className="
            flex items-center gap-1.5
            text-xs font-body
            text-[var(--g-title-text-muted)]
            hover:text-[var(--g-accent-gold)]
            transition-colors duration-200
          "
        >
          <motion.span
            animate={{ rotate: showAdvanced ? 180 : 0 }}
            transition={{ duration: 0.2 }}
          >
            <ChevronDown className="w-3.5 h-3.5" />
          </motion.span>
          {t('landing.advancedOptions')}
        </button>

        <AnimatePresence>
          {showAdvanced && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: 'auto', opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.25 }}
              className="overflow-hidden"
            >
              <div className="pt-3 space-y-3">
                {/* Player ID */}
                <label className="block">
                  <span className="text-xs font-body text-[var(--g-title-text-muted)] mb-1 block">
                    {t('landing.playerId')}
                  </span>
                  <input
                    type="text"
                    value={userId}
                    onChange={(e) => setUserId(e.target.value)}
                    className="
                      w-full px-3 py-2
                      font-body text-sm
                      bg-[rgba(26,21,16,0.6)]
                      text-[var(--g-title-text-primary)]
                      border border-[var(--g-title-border)]
                      rounded-lg
                      focus:border-[var(--g-accent-gold)]
                      focus:outline-none
                      transition-colors
                      placeholder:text-[var(--g-title-text-muted)]
                    "
                    placeholder="player-001"
                  />
                </label>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
};

export default SessionCreator;
