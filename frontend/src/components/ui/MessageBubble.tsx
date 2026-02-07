/**
 * Message bubble component - Golden theme
 */
import React from 'react';
import { motion } from 'framer-motion';
import type { MessageType } from '../../types';

interface MessageBubbleProps {
  speaker: string;
  content: React.ReactNode;
  type: MessageType;
  timestamp?: Date;
  metadata?: {
    reaction?: string;
  };
  animateEntry?: boolean;
}

const typeStyles: Record<MessageType, {
  borderColor: string;
  bgColor: string;
  labelColor: string;
  align: 'left' | 'right' | 'center';
}> = {
  gm: {
    borderColor: 'border-l-g-bubble-gm-border',
    bgColor: 'bg-g-bubble-gm-bg',
    labelColor: 'text-g-gold',
    align: 'left',
  },
  npc: {
    borderColor: 'border-l-g-bubble-npc-border',
    bgColor: 'bg-g-bubble-npc-bg',
    labelColor: 'text-g-cyan',
    align: 'left',
  },
  player: {
    borderColor: 'border-l-g-bubble-player-border',
    bgColor: 'bg-g-bubble-player-bg',
    labelColor: 'text-g-green',
    align: 'right',
  },
  teammate: {
    borderColor: 'border-l-g-bubble-teammate-border',
    bgColor: 'bg-g-bubble-teammate-bg',
    labelColor: 'text-g-green',
    align: 'left',
  },
  system: {
    borderColor: 'border-l-g-text-muted',
    bgColor: 'bg-g-bubble-system-bg',
    labelColor: 'text-g-text-muted',
    align: 'center',
  },
  combat: {
    borderColor: 'border-l-g-bubble-combat-border',
    bgColor: 'bg-g-bubble-combat-bg',
    labelColor: 'text-g-red',
    align: 'left',
  },
};

const defaultStyle = {
  borderColor: 'border-l-g-text-muted',
  bgColor: 'bg-g-bg-surface',
  labelColor: 'text-g-text-muted',
  align: 'left' as const,
};

export const MessageBubble: React.FC<MessageBubbleProps> = ({
  speaker,
  content,
  type,
  timestamp,
  metadata,
  animateEntry = true,
}) => {
  const style = typeStyles[type] || defaultStyle;

  if (!typeStyles[type]) {
    console.warn(`MessageBubble: Unknown message type "${type}", using default style`);
  }

  const alignmentClass =
    style.align === 'right'
      ? 'ml-auto'
      : style.align === 'center'
      ? 'mx-auto'
      : 'mr-auto';

  const bubbleContent = (
    <div
      className={`
        relative max-w-[85%] p-4 mb-3
        border border-g-border-subtle
        border-l-[3px] ${style.borderColor}
        ${style.bgColor}
        rounded-xl
        shadow-g-sm
        font-body text-g-text-primary
      `}
    >
      {/* Speaker label - inline */}
      <div className={`flex items-center gap-2 mb-1.5`}>
        <span className={`text-sm font-semibold ${style.labelColor}`}>
          {speaker}
        </span>
        {metadata?.reaction && (
          <span className="text-xs text-g-text-muted italic">
            ({metadata.reaction})
          </span>
        )}
      </div>

      {/* Message content */}
      <div className="leading-relaxed whitespace-pre-wrap">
        {content}
      </div>

      {/* Timestamp */}
      {timestamp && (
        <div
          className={`
            text-xs text-g-text-muted mt-2
            ${style.align === 'right' ? 'text-left' : 'text-right'}
          `}
        >
          {timestamp.toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </div>
      )}
    </div>
  );

  if (animateEntry) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 12 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', stiffness: 300, damping: 30 }}
        className={alignmentClass}
      >
        {bubbleContent}
      </motion.div>
    );
  }

  return <div className={alignmentClass}>{bubbleContent}</div>;
};

export default MessageBubble;
