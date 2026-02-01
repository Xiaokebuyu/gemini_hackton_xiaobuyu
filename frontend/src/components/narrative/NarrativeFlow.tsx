/**
 * Narrative flow container - displays message history
 */
import React, { useEffect, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { AnimatePresence } from 'framer-motion';
import { useChatStore, useGameStore } from '../../stores';
import NarrativeMessage from './NarrativeMessage';
import ActionButtons from './ActionButtons';
import LoadingSpinner from '../shared/LoadingSpinner';

interface NarrativeFlowProps {
  className?: string;
}

export const NarrativeFlow: React.FC<NarrativeFlowProps> = ({
  className = '',
}) => {
  const { t } = useTranslation();
  const { messages, isLoading } = useChatStore();
  const { availableActions } = useGameStore();
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
        sketch-scrollbar
        p-4
        ${className}
      `}
    >
      {/* Empty state */}
      {messages.length === 0 && !isLoading && (
        <div className="flex flex-col items-center justify-center h-full text-center">
          <div className="text-6xl mb-4">🏰</div>
          <h2 className="font-handwritten text-xl text-sketch-accent-gold mb-2">
            {t('narrative.welcome')}
          </h2>
          <p className="text-sketch-ink-secondary max-w-md font-handwritten">
            {t('narrative.welcomeHint')}
          </p>
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
          <span className="text-sketch-ink-secondary text-sm italic font-handwritten">
            {t('narrative.gmThinking')}
          </span>
        </div>
      )}

      {/* Available actions */}
      {!isLoading && availableActions.length > 0 && (
        <div className="mt-6 pt-4 border-t border-sketch-ink-faint">
          <h4 className="text-xs text-sketch-ink-muted uppercase tracking-wide mb-3 font-handwritten">
            {t('actions.title')}
          </h4>
          <ActionButtons actions={availableActions} />
        </div>
      )}

      {/* Scroll anchor */}
      <div ref={bottomRef} />
    </div>
  );
};

export default NarrativeFlow;
