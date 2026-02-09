/**
 * Single narrative message component - Golden theme
 */
import React, { useState } from 'react';
import type { NarrativeMessage as NarrativeMessageType } from '../../types';
import TypewriterText from './TypewriterText';
import GMOptions from './GMOptions';
import { MessageBubble } from '../ui';
import { parseGMNarration } from '../../utils/narrationParser';
import { useStreamGameInput } from '../../api';

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
  const [selectedOption, setSelectedOption] = useState<string | null>(null);
  const [isTypingComplete, setIsTypingComplete] = useState(!isLatest);
  const { sendInput } = useStreamGameInput();

  // Parse GM messages to split text from options
  const parsed = message.type === 'gm' ? parseGMNarration(message.content) : null;
  const narrativeText = parsed ? parsed.text : message.content;

  const handleOptionSelect = (option: { id: string; label: string }) => {
    setSelectedOption(option.id);
    sendInput(option.label);
  };

  // For GM messages, use typewriter effect when latest
  const content =
    isLatest && message.type === 'gm' ? (
      <>
        <TypewriterText
          text={narrativeText}
          speed={15}
          onComplete={() => setIsTypingComplete(true)}
        />
        {isTypingComplete && parsed && parsed.options.length > 0 && (
          <GMOptions
            options={parsed.options}
            onSelect={handleOptionSelect}
            disabled={selectedOption != null}
            selectedId={selectedOption}
          />
        )}
      </>
    ) : (
      <>
        <p className="whitespace-pre-wrap">{narrativeText}</p>
        {/* Show selected option for historical GM messages */}
        {parsed && parsed.options.length > 0 && !isLatest && (
          <GMOptions
            options={parsed.options}
            onSelect={handleOptionSelect}
            disabled={true}
            selectedId={null}
          />
        )}
      </>
    );

  return (
    <MessageBubble
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
