/**
 * Galgame-style teammate response zone — full-width vertical bar layout
 */
import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { MessageCircle } from 'lucide-react';
import type { NarrativeMessage } from '../../types';

interface GalgameTeammateZoneProps {
  messages: NarrativeMessage[];
  visible: boolean;
}

export const GalgameTeammateZone: React.FC<GalgameTeammateZoneProps> = ({
  messages,
  visible,
}) => {
  // Only show teammates that actually have content
  const withContent = messages.filter((m) => m.content.trim());
  if (withContent.length === 0) return null;

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
          {withContent.map((msg, i) => (
            <motion.div
              key={msg.id}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.25, delay: i * 0.08 }}
              className="w-full flex items-start gap-3 bg-g-bubble-teammate-bg border border-g-bubble-teammate-border rounded-lg px-4 py-2.5"
            >
              {/* Left: name label */}
              <div className="flex items-center gap-1.5 flex-shrink-0 pt-0.5">
                <MessageCircle className="w-3.5 h-3.5 text-g-bubble-teammate-border" />
                <span className="text-xs font-medium text-g-bubble-teammate-border whitespace-nowrap">
                  {msg.speaker}
                </span>
                {msg.metadata?.reaction && (
                  <span className="text-xs g-text-muted italic">
                    {msg.metadata.reaction}
                  </span>
                )}
              </div>
              {/* Right: content */}
              <p className="text-sm text-[var(--g-text-primary)] whitespace-pre-wrap flex-1 min-w-0">
                {msg.content}
              </p>
            </motion.div>
          ))}
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default GalgameTeammateZone;
