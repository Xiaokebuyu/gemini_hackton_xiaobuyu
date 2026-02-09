/**
 * Galgame-style fixed display — replaces NarrativeFlow
 * Shows only the current round (last player action → GM response → teammate replies).
 * ChatInput + QuickActions are integrated at the bottom.
 */
import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { AnimatePresence, motion } from 'framer-motion';
import { BookOpen, Landmark, User } from 'lucide-react';
import { useChatStore, useGameStore, useCombatStore } from '../../stores';
import type { NarrativeMessage } from '../../types';
import TypewriterText from './TypewriterText';
import GMOptions from './GMOptions';
import GalgameTeammateZone from './GalgameTeammateZone';
import CombatTriggerCard from '../combat/CombatTriggerCard';
import LoadingSpinner from '../shared/LoadingSpinner';
import ChatInput from '../input/ChatInput';
import QuickActions from '../input/QuickActions';
import { parseGMNarration } from '../../utils/narrationParser';
import { useStreamGameInput } from '../../api';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

interface CurrentRound {
  playerMessage: NarrativeMessage | null;
  gmNarration: NarrativeMessage | null;
  teammateResponses: NarrativeMessage[];
}

function getCurrentRound(messages: NarrativeMessage[]): CurrentRound {
  // Find last player message
  let lastPlayerIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].type === 'player') {
      lastPlayerIdx = i;
      break;
    }
  }

  if (lastPlayerIdx === -1) {
    // No player message yet (opening narration) — show last GM message
    const lastGM = [...messages].reverse().find((m) => m.type === 'gm') ?? null;
    return { playerMessage: null, gmNarration: lastGM, teammateResponses: [] };
  }

  const roundMessages = messages.slice(lastPlayerIdx);
  return {
    playerMessage: roundMessages.find((m) => m.type === 'player') ?? null,
    gmNarration: roundMessages.find((m) => m.type === 'gm') ?? null,
    teammateResponses: roundMessages.filter((m) => m.type === 'teammate'),
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const GalgameDisplay: React.FC<{ className?: string }> = ({
  className = '',
}) => {
  const { t } = useTranslation();
  const { messages, isLoading, streamingMessageId } = useChatStore();
  const { combatId } = useGameStore();
  const { isActive: isCombatActive } = useCombatStore();
  const { sendInput } = useStreamGameInput();

  const round = useMemo(() => getCurrentRound(messages), [messages]);
  const roundId = round.playerMessage?.id ?? round.gmNarration?.id ?? '__empty__';

  // Track whether the GM typewriter has finished
  const [isTypingComplete, setIsTypingComplete] = useState(false);
  const [selectedOption, setSelectedOption] = useState<string | null>(null);

  // Is the current GM message being streamed right now?
  const isGmStreaming = !!(round.gmNarration && streamingMessageId === round.gmNarration.id);

  // Track whether the current GM message was delivered via streaming
  // so we skip the typewriter re-animation when SSE ends
  const wasStreamedRef = React.useRef(false);

  // Reset typing state whenever the round changes
  useEffect(() => {
    setIsTypingComplete(false);
    setSelectedOption(null);
    wasStreamedRef.current = false;
  }, [roundId]);

  // When GM streaming finishes, mark typing complete immediately (skip typewriter animation)
  const prevGmStreamingRef = React.useRef(false);
  useEffect(() => {
    if (prevGmStreamingRef.current && !isGmStreaming) {
      // Was streaming, now done — mark complete immediately
      setIsTypingComplete(true);
      wasStreamedRef.current = true;
    }
    prevGmStreamingRef.current = isGmStreaming;
  }, [isGmStreaming]);

  // Parse GM narration
  const parsed = round.gmNarration?.type === 'gm'
    ? parseGMNarration(round.gmNarration.content)
    : null;
  const narrativeText = parsed ? parsed.text : (round.gmNarration?.content ?? '');

  const handleOptionSelect = (option: { id: string; label: string }) => {
    setSelectedOption(option.id);
    sendInput(option.label);
  };

  // ---- Empty state (no messages at all) ----
  if (messages.length === 0 && !isLoading) {
    return (
      <div className={`h-full flex flex-col p-4 ${className}`}>
        <div className="flex-1 flex flex-col items-center justify-center text-center">
          <div className="border-2 border-g-border rounded-xl p-8 max-w-lg shadow-g-md bg-g-bg-surface-alt">
            <div className="w-20 h-20 rounded-full bg-g-gold/15 flex items-center justify-center mb-6 mx-auto border-2 border-g-border">
              <Landmark className="w-10 h-10 text-g-gold" />
            </div>
            <h2 className="font-heading text-2xl text-g-gold mb-3">
              {t('narrative.welcome')}
            </h2>
            <p className="text-g-text-secondary max-w-md font-body">
              {t('narrative.welcomeHint')}
            </p>
            <div className="g-divider mt-6">
              <span className="text-g-gold text-xs">&#9670;</span>
            </div>
          </div>
        </div>

        {/* Input even in empty state */}
        <div className="flex-shrink-0 mt-4">
          <ChatInput />
          <QuickActions className="mt-3" />
        </div>
      </div>
    );
  }

  // ---- Main display ----
  return (
    <div className={`h-full flex flex-col p-4 ${className}`}>
      <AnimatePresence mode="wait">
        <motion.div
          key={roundId}
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
          className="flex-1 min-h-0 flex flex-col"
        >
          {/* ---- Player action summary bar ---- */}
          {round.playerMessage && (
            <motion.div
              initial={{ opacity: 0, x: -12 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.25, delay: 0.05 }}
              className="flex-shrink-0 flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-g-gold/10 border border-g-gold/30">
              <User className="w-4 h-4 text-g-gold flex-shrink-0" />
              <span className="text-sm text-g-gold font-medium truncate">
                {round.playerMessage.content}
              </span>
            </motion.div>
          )}

          {/* ---- GM narration area ---- */}
          <div className="flex-1 min-h-0 overflow-y-auto g-scrollbar">
            {isLoading && !round.gmNarration && (
              <div className="bg-[var(--g-bubble-gm-bg)] border-l-[3px] border-l-[var(--g-accent-gold)] rounded-xl px-6 py-5 min-h-[80px] shadow-g-gold animate-pulse flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-g-gold animate-bounce" style={{ animationDelay: '0s' }} />
                  <span className="w-2 h-2 rounded-full bg-g-gold animate-bounce" style={{ animationDelay: '0.2s' }} />
                  <span className="w-2 h-2 rounded-full bg-g-gold animate-bounce" style={{ animationDelay: '0.4s' }} />
                </div>
                <span className="text-g-text-secondary text-sm italic font-body">
                  {t('narrative.gmThinking')}
                </span>
              </div>
            )}

            {round.gmNarration && (
              <div className="bg-[var(--g-bubble-gm-bg)] border-l-[3px] border-l-[var(--g-accent-gold)] rounded-xl px-6 py-5 min-h-[120px] shadow-g-gold g-inner-glow">
                {/* GM label */}
                <div className="flex items-center gap-2 mb-4 pb-2 border-b border-[var(--g-accent-gold)]/20">
                  <BookOpen className="w-5 h-5 text-[var(--g-accent-gold)]" />
                  <span className="text-sm font-semibold text-[var(--g-accent-gold)] tracking-wide">
                    {t('speaker.gm')}
                  </span>
                </div>
                {/* Narration text */}
                <div className="whitespace-pre-wrap text-[var(--g-text-primary)] font-body leading-relaxed">
                  {isGmStreaming ? (
                    <>
                      {parseGMNarration(round.gmNarration.content).text}
                      <span className="inline-block w-2 h-4 ml-0.5 bg-g-gold/70 animate-pulse align-text-bottom" />
                    </>
                  ) : (
                    <TypewriterText
                      text={narrativeText}
                      speed={15}
                      skipAnimation={wasStreamedRef.current}
                      onComplete={() => setIsTypingComplete(true)}
                    />
                  )}
                </div>
              </div>
            )}

            {/* GM options — show after typing completes */}
            {isTypingComplete && parsed && parsed.options.length > 0 && (
              <GMOptions
                options={parsed.options}
                onSelect={handleOptionSelect}
                disabled={selectedOption != null}
                selectedId={selectedOption}
              />
            )}

            {/* Combat trigger card */}
            {combatId && !isCombatActive && <CombatTriggerCard />}

            {/* Teammate thinking indicator */}
            {isTypingComplete && round.teammateResponses.length === 0 && isLoading && (
              <div className="mt-3 flex items-center gap-2 px-4 py-2 rounded-lg bg-g-bubble-teammate-bg border border-g-bubble-teammate-border">
                <div className="flex items-center gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-g-bubble-teammate-border animate-bounce" style={{ animationDelay: '0s' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-g-bubble-teammate-border animate-bounce" style={{ animationDelay: '0.2s' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-g-bubble-teammate-border animate-bounce" style={{ animationDelay: '0.4s' }} />
                </div>
                <span className="text-xs text-g-text-muted italic font-body">
                  {t('narrative.teammateThinking', '队友正在思考...')}
                </span>
              </div>
            )}

            {/* ---- Teammate response zone (after GM content, before input) ---- */}
            <GalgameTeammateZone
              messages={round.teammateResponses}
              visible={isTypingComplete}
            />
          </div>
        </motion.div>
      </AnimatePresence>

      {/* ---- Input area (outside AnimatePresence — persistent across rounds) ---- */}
      <div className="flex-shrink-0 mt-3">
        <ChatInput />
        <QuickActions className="mt-3" />
      </div>
    </div>
  );
};

export default GalgameDisplay;
