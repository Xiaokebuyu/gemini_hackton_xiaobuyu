/**
 * Main chat input component - Golden theme
 */
import React, { useState, useRef, useEffect, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { Send, Loader2 } from 'lucide-react';
import { useStreamGameInput } from '../../api';
import { useChatStore } from '../../stores';
import { Input } from '../ui';
import { Button } from '../ui';

interface ChatInputProps {
  className?: string;
}

export const ChatInput: React.FC<ChatInputProps> = ({ className = '' }) => {
  const { t } = useTranslation();
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { isLoading } = useChatStore();
  const { sendInput } = useStreamGameInput();

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

  return (
    <div className={className}>
      {/* Input area */}
      <div className="relative flex items-end gap-2">
        <div
          className="
            flex-1
            relative
            border-2
            rounded-xl
            transition-all duration-200
            border-g-border-strong focus-within:border-g-gold
            bg-g-bg-input
          "
        >
          <Input
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={t('chat.placeholder.say')}
            disabled={isLoading}
            rows={1}
            className="border-0"
            style={{ minHeight: '44px', maxHeight: '150px' }}
          />
        </div>

        {/* Send button */}
        <Button
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
        </Button>
      </div>

      {/* Hint */}
      <div className="mt-2 text-xs text-g-text-muted font-body">
        {t('chat.sendHint')}
      </div>
    </div>
  );
};

export default ChatInput;
