/**
 * Teammate response bubble component
 */
import React from 'react';
import { motion } from 'framer-motion';
import type { TeammateResponseResult } from '../../types';

interface TeammateResponseProps {
  response: TeammateResponseResult;
  delay?: number;
}

export const TeammateResponse: React.FC<TeammateResponseProps> = ({
  response,
  delay = 0,
}) => {
  // If no response, show a subtle "didn't respond" indicator
  if (!response.response) {
    return (
      <motion.div
        initial={{ opacity: 0, x: -20 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.3, delay }}
        className="flex items-center gap-2 text-xs text-[var(--color-text-muted)] italic"
      >
        <span className="text-base">💭</span>
        <span>{response.name} stays silent</span>
        {response.reaction && (
          <span className="text-[var(--color-text-muted)]">
            ({response.reaction})
          </span>
        )}
      </motion.div>
    );
  }

  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, delay }}
      className="flex items-start gap-3 mb-3"
    >
      {/* Avatar placeholder */}
      <div
        className="
          w-8 h-8 rounded-full
          bg-bg-card
          flex items-center justify-center
          flex-shrink-0
          text-lg
        "
      >
        💭
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        {/* Header */}
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-medium text-[#90ee90]">
            {response.name}
          </span>
          {response.reaction && (
            <span className="text-xs text-[var(--color-text-muted)] italic">
              {response.reaction}
            </span>
          )}
        </div>

        {/* Response bubble */}
        <div
          className="
            bg-gradient-to-r from-[#90ee90]/10 to-transparent
            border-l-2 border-[#90ee90]
            rounded-r-lg
            p-3
          "
        >
          <p className="text-sm text-[var(--color-text-primary)] whitespace-pre-wrap">
            {response.response}
          </p>
        </div>

        {/* Metadata */}
        {response.model_used && (
          <div className="text-xs text-[var(--color-text-muted)] mt-1 flex items-center gap-2">
            <span>{response.latency_ms}ms</span>
            {response.thinking_level && (
              <span className="badge badge-cyan text-[10px]">
                {response.thinking_level}
              </span>
            )}
          </div>
        )}
      </div>
    </motion.div>
  );
};

export default TeammateResponse;
