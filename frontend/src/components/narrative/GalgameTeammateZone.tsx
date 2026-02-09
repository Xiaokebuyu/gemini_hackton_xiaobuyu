/**
 * Galgame-style teammate response zone — vertical card layout
 *
 * Supports streaming: shows cards even for empty-content messages (streaming in progress),
 * with a blinking cursor indicator.
 */
import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MessageCircle } from 'lucide-react';
import type { NarrativeMessage } from '../../types';
import { useChatStore } from '../../stores';

interface GalgameTeammateZoneProps {
  messages: NarrativeMessage[];
  visible: boolean;
}

export const GalgameTeammateZone: React.FC<GalgameTeammateZoneProps> = ({
  messages,
  visible,
}) => {
  const { streamingMessageId } = useChatStore();

  // Show all messages — including empty ones that are currently streaming
  if (messages.length === 0) return null;

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 12 }}
          transition={{ duration: 0.3 }}
          className="mt-4 space-y-2"
        >
          {messages.map((msg, i) => {
            const isStreaming = streamingMessageId === msg.id;

            return (
              <motion.div
                key={msg.id}
                initial={{ opacity: 0, y: 8, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ duration: 0.25, delay: i * 0.12 }}
                whileHover={{ scale: 1.01, boxShadow: 'var(--g-shadow-md)' }}
                className="w-full bg-g-bubble-teammate-bg border border-g-bubble-teammate-border rounded-lg px-4 py-2.5 card-hover"
              >
                {/* Top: name label row */}
                <div className="flex items-center gap-1.5 mb-1.5">
                  <MessageCircle className="w-3.5 h-3.5 text-g-bubble-teammate-border" />
                  <span className="text-xs font-semibold text-g-bubble-teammate-border whitespace-nowrap">
                    {msg.speaker}
                  </span>
                  {msg.metadata?.reaction && (
                    <span className="text-xs text-g-text-muted italic ml-1">
                      {msg.metadata.reaction}
                    </span>
                  )}
                </div>
                {/* Bottom: content */}
                <p className="text-sm text-[var(--g-text-primary)] whitespace-pre-wrap leading-relaxed">
                  {msg.content}
                  {isStreaming && (
                    <span className="inline-block w-2 h-4 ml-0.5 bg-g-gold/70 animate-pulse align-text-bottom" />
                  )}
                </p>
              </motion.div>
            );
          })}
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default GalgameTeammateZone;
