/**
 * Main chat input component - using Sketch style
 */
import React, { useState, useRef, useEffect, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Send, Loader2 } from 'lucide-react';
import { useGameInput } from '../../api';
import { useGameStore, useChatStore } from '../../stores';
import ChatModeToggle from './ChatModeToggle';
import SketchInput from '../sketch/SketchInput';
import SketchButton from '../sketch/SketchButton';

interface ChatInputProps {
  className?: string;
}

export const ChatInput: React.FC<ChatInputProps> = ({ className = '' }) => {
  const { t } = useTranslation();
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { chatMode } = useGameStore();
  const { isLoading } = useChatStore();
  const { sendInput } = useGameInput();

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(
        textareaRef.current.scrollHeight,
        150
      )}px`;
    }
  }, [input]);

  // Focus on mount
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  const handleSubmit = () => {
    const trimmedInput = input.trim();
    if (trimmedInput && !isLoading) {
      sendInput(trimmedInput);
      setInput('');
      // Reset textarea height
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Submit on Enter (without Shift)
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const placeholderText =
    chatMode === 'think'
      ? t('chat.placeholder.think')
      : t('chat.placeholder.say');

  return (
    <div className={className}>
      {/* Mode toggle */}
      <div className="flex items-center justify-between mb-3">
        <ChatModeToggle />
        <span className="text-xs text-sketch-ink-muted font-handwritten">
          {chatMode === 'think' ? `🧠 ${t('chat.modeHint.think')}` : `🗣️ ${t('chat.modeHint.say')}`}
        </span>
      </div>

      {/* Input area */}
      <div className="relative flex items-end gap-2">
        <div
          className={`
            flex-1
            relative
            border-2
            transition-all duration-200
            ${
              chatMode === 'think'
                ? 'border-sketch-accent-purple/50 focus-within:border-sketch-accent-purple'
                : 'border-sketch-accent-green/50 focus-within:border-sketch-accent-green'
            }
            bg-sketch-bg-input
          `}
          style={{ borderRadius: '4px' }}
        >
          <SketchInput
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholderText}
            disabled={isLoading}
            rows={1}
            className="border-0"
            style={{ minHeight: '44px', maxHeight: '150px' }}
          />
        </div>

        {/* Send button */}
        <SketchButton
          onClick={handleSubmit}
          disabled={!input.trim() || isLoading}
          variant={input.trim() && !isLoading ? 'primary' : 'secondary'}
          className="w-12 h-12 flex items-center justify-center"
          title={t('chat.send')}
        >
          {isLoading ? (
            <Loader2 className="w-5 h-5 animate-spin" />
          ) : (
            <Send className="w-5 h-5" />
          )}
        </SketchButton>
      </div>

      {/* Hint */}
      <div className="mt-2 text-xs text-sketch-ink-muted font-handwritten">
        {t('chat.sendHint')}
      </div>
    </div>
  );
};

export default ChatInput;
