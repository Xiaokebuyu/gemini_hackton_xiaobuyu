/**
 * Hand-drawn style message bubble component
 */
import React from 'react';
import { motion } from 'framer-motion';
import type { MessageType } from '../../types';

interface SketchMessageBubbleProps {
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
    borderColor: 'border-sketch-accent-gold',
    bgColor: 'bg-sketch-bubble-gm',
    labelColor: 'text-sketch-accent-gold',
    align: 'left',
  },
  npc: {
    borderColor: 'border-sketch-accent-cyan',
    bgColor: 'bg-sketch-bubble-npc',
    labelColor: 'text-sketch-accent-cyan',
    align: 'left',
  },
  player: {
    borderColor: 'border-sketch-accent-green',
    bgColor: 'bg-sketch-bubble-player',
    labelColor: 'text-sketch-accent-green',
    align: 'right',
  },
  teammate: {
    borderColor: 'border-sketch-accent-green/70',
    bgColor: 'bg-sketch-bubble-teammate',
    labelColor: 'text-sketch-accent-green',
    align: 'left',
  },
  system: {
    borderColor: 'border-sketch-ink-muted',
    bgColor: 'bg-sketch-bubble-system',
    labelColor: 'text-sketch-ink-muted',
    align: 'center',
  },
  combat: {
    borderColor: 'border-sketch-accent-red',
    bgColor: 'bg-sketch-bubble-combat',
    labelColor: 'text-sketch-accent-red',
    align: 'left',
  },
};

// Default fallback style for unknown message types
const defaultStyle = {
  borderColor: 'border-sketch-ink-muted',
  bgColor: 'bg-sketch-bg-panel',
  labelColor: 'text-sketch-ink-muted',
  align: 'left' as const,
};

export const SketchMessageBubble: React.FC<SketchMessageBubbleProps> = ({
  speaker,
  content,
  type,
  timestamp,
  metadata,
  animateEntry = true,
}) => {
  const style = typeStyles[type] || defaultStyle;

  // Log warning for unknown message types (development aid)
  if (!typeStyles[type]) {
    console.warn(`SketchMessageBubble: Unknown message type "${type}", using default style`);
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
        border-2 ${style.borderColor} ${style.bgColor}
        font-sketch-body text-sketch-ink-primary
      `}
      style={{
        // Slight rotation for hand-drawn feel
        transform: style.align === 'right' ? 'rotate(0.3deg)' : 'rotate(-0.3deg)',
        // Irregular shape
        clipPath: 'polygon(0% 2%, 99% 0%, 100% 97%, 1% 100%)',
        borderRadius: '4px',
      }}
    >
      {/* Speaker label */}
      <div
        className={`
          absolute -top-3 ${style.align === 'right' ? 'right-4' : 'left-4'}
          px-2 py-0.5
          ${style.bgColor}
          font-handwritten text-sm font-medium
          ${style.labelColor}
        `}
      >
        {speaker}
        {metadata?.reaction && (
          <span className="ml-2 text-xs text-sketch-ink-muted italic">
            ({metadata.reaction})
          </span>
        )}
      </div>

      {/* Message content */}
      <div className="mt-1 leading-relaxed whitespace-pre-wrap">
        {content}
      </div>

      {/* Timestamp */}
      {timestamp && (
        <div
          className={`
            text-xs text-sketch-ink-muted mt-2
            ${style.align === 'right' ? 'text-left' : 'text-right'}
          `}
        >
          {timestamp.toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </div>
      )}

      {/* Decorative pencil mark */}
      <div className="absolute -bottom-1 -right-1 text-xs opacity-30">

      </div>
    </div>
  );

  if (animateEntry) {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
        className={alignmentClass}
      >
        {bubbleContent}
      </motion.div>
    );
  }

  return <div className={alignmentClass}>{bubbleContent}</div>;
};

export default SketchMessageBubble;
