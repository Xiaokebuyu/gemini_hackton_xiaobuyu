/**
 * Single narrative message component - using Sketch style
 */
import React, { useState } from 'react';
import type { NarrativeMessage as NarrativeMessageType } from '../../types';
import TypewriterText from './TypewriterText';
import SketchMessageBubble from '../sketch/SketchMessageBubble';

interface NarrativeMessageProps {
  message: NarrativeMessageType;
  isLatest?: boolean;
  animateEntry?: boolean;
}

export const NarrativeMessage: React.FC<NarrativeMessageProps> = ({
  message,
  isLatest = false,
  animateEntry = true,
}) => {
  const [, setIsTypingComplete] = useState(!isLatest);

  // For GM messages, use typewriter effect when latest
  const content =
    isLatest && message.type === 'gm' ? (
      <TypewriterText
        text={message.content}
        speed={15}
        onComplete={() => setIsTypingComplete(true)}
      />
    ) : (
      <p className="whitespace-pre-wrap">{message.content}</p>
    );

  return (
    <SketchMessageBubble
      speaker={message.speaker}
      content={content}
      type={message.type}
      timestamp={message.timestamp}
      metadata={message.metadata}
      animateEntry={animateEntry}
    />
  );
};

export default NarrativeMessage;
