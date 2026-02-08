/**
 * Narrative flow container - displays message history
 */
import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { AnimatePresence } from 'framer-motion';
import { Landmark } from 'lucide-react';
import { useChatStore, useGameStore, useCombatStore } from '../../stores';
import NarrativeMessage from './NarrativeMessage';
import CombatTriggerCard from '../combat/CombatTriggerCard';
import LoadingSpinner from '../shared/LoadingSpinner';

interface NarrativeFlowProps {
  className?: string;
}

export const NarrativeFlow: React.FC<NarrativeFlowProps> = ({
  className = '',
}) => {
  const { t } = useTranslation();
  const { messages, isLoading } = useChatStore();
  const { combatId } = useGameStore();
  const { isActive: isCombatActive } = useCombatStore();
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (bottomRef.current) {
      bottomRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages, isLoading]);

  return (
    <div
      ref={scrollRef}
      className={`
        h-full
        overflow-y-auto
        g-scrollbar
        p-4
        ${className}
      `}
    >
      {/* Empty state */}
      {messages.length === 0 && !isLoading && (
        <div className="flex flex-col items-center justify-center h-full text-center">
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
      )}

      {/* Messages */}
      <AnimatePresence>
        {messages.map((message, index) => (
          <NarrativeMessage
            key={message.id}
            message={message}
            isLatest={index === messages.length - 1 && !isLoading}
            animateEntry={index >= messages.length - 3}
          />
        ))}
      </AnimatePresence>

      {/* Loading indicator */}
      {isLoading && (
        <div className="flex items-center gap-3 py-4">
          <LoadingSpinner size="sm" />
          <span className="g-text-secondary text-sm italic font-body">
            {t('narrative.gmThinking')}
          </span>
        </div>
      )}

      {/* Combat trigger card */}
      {combatId && !isCombatActive && <CombatTriggerCard />}

      {/* Scroll anchor */}
      <div ref={bottomRef} />
    </div>
  );
};

export default NarrativeFlow;
