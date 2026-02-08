/**
 * Galgame-style fixed display — replaces NarrativeFlow
 * Shows only the current round (last player action → GM response → teammate replies).
 * ChatInput + QuickActions are integrated at the bottom.
 */
import React, { useState, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { AnimatePresence, motion } from 'framer-motion';
import { Landmark, User } from 'lucide-react';
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
import { useGameInput } from '../../api';

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
  const { messages, isLoading } = useChatStore();
  const { combatId } = useGameStore();
  const { isActive: isCombatActive } = useCombatStore();
  const { sendInput } = useGameInput();

  const round = useMemo(() => getCurrentRound(messages), [messages]);
  const roundId = round.playerMessage?.id ?? round.gmNarration?.id ?? '__empty__';

  // Track whether the GM typewriter has finished
  const [isTypingComplete, setIsTypingComplete] = useState(false);
  const [selectedOption, setSelectedOption] = useState<string | null>(null);

  // Reset typing state whenever the round changes
  useEffect(() => {
    setIsTypingComplete(false);
    setSelectedOption(null);
  }, [roundId]);

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
            <p className="g-text-secondary max-w-md font-body">
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
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.25 }}
          className="flex-1 min-h-0 flex flex-col"
        >
          {/* ---- Player action summary bar ---- */}
          {round.playerMessage && (
            <div className="flex-shrink-0 flex items-center gap-2 mb-3 px-3 py-2 rounded-lg bg-g-gold/10 border border-g-gold/30">
              <User className="w-4 h-4 text-g-gold flex-shrink-0" />
              <span className="text-sm text-g-gold font-medium truncate">
                {round.playerMessage.content}
              </span>
            </div>
          )}

          {/* ---- GM narration area ---- */}
          <div className="flex-1 min-h-0 overflow-y-auto g-scrollbar">
            {isLoading && !round.gmNarration && (
              <div className="flex items-center gap-3 py-4">
                <LoadingSpinner size="sm" />
                <span className="g-text-secondary text-sm italic font-body">
                  {t('narrative.gmThinking')}
                </span>
              </div>
            )}

            {round.gmNarration && (
              <div className="whitespace-pre-wrap text-[var(--g-text-primary)] font-body leading-relaxed">
                <TypewriterText
                  text={narrativeText}
                  speed={15}
                  onComplete={() => setIsTypingComplete(true)}
                />
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
