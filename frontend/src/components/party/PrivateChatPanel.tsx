/**
 * Private Chat Panel — full-screen overlay for 1-on-1 character conversations
 */
import React, { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Send } from 'lucide-react';
import { usePrivateChatStore } from '../../stores/privateChatStore';
import { usePrivateChat } from '../../api/hooks/usePrivateChat';

export const PrivateChatPanel: React.FC = () => {
  const { isOpen, targetName, messages, isStreaming, closeChat } =
    usePrivateChatStore();
  const { sendMessage } = usePrivateChat();
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Focus input when panel opens
  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 200);
    }
  }, [isOpen]);

  const handleSend = () => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    setInput('');
    sendMessage(trimmed);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ opacity: 0, y: 40 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: 40 }}
          transition={{ duration: 0.25 }}
          className="fixed inset-0 z-50 flex flex-col bg-g-bg-base"
        >
          {/* Header */}
          <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 border-b-2 border-g-border bg-g-bg-surface">
            <h3 className="font-heading text-lg text-g-gold truncate">
              {targetName}
            </h3>
            <button
              onClick={closeChat}
              className="p-2 hover:bg-g-gold/20 rounded-lg transition-colors"
            >
              <X className="w-5 h-5 text-g-gold" />
            </button>
          </div>

          {/* Messages */}
          <div
            ref={scrollRef}
            className="flex-1 overflow-y-auto g-scrollbar px-4 py-3 space-y-3"
          >
            {messages.length === 0 && (
              <p className="text-center text-g-text-muted text-sm italic mt-8">
                开始与 {targetName} 的私聊...
              </p>
            )}
            {messages.map((msg) => (
              <div
                key={msg.id}
                className={`flex ${msg.role === 'player' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`
                    max-w-[80%] px-3 py-2 rounded-xl text-sm font-body leading-relaxed
                    ${
                      msg.role === 'player'
                        ? 'bg-g-gold/20 text-g-gold border border-g-gold/30'
                        : 'bg-g-bg-surface border border-g-border text-[var(--g-text-primary)]'
                    }
                  `}
                >
                  <span className="whitespace-pre-wrap">{msg.content}</span>
                  {msg.role === 'character' && isStreaming && msg.content === '' && (
                    <span className="inline-block w-2 h-4 ml-0.5 bg-g-gold/70 animate-pulse align-text-bottom" />
                  )}
                  {msg.role === 'character' &&
                    isStreaming &&
                    msg.id === messages[messages.length - 1]?.id &&
                    msg.content !== '' && (
                      <span className="inline-block w-2 h-4 ml-0.5 bg-g-gold/70 animate-pulse align-text-bottom" />
                    )}
                </div>
              </div>
            ))}
          </div>

          {/* Input */}
          <div className="flex-shrink-0 px-4 py-3 border-t-2 border-g-border bg-g-bg-surface">
            <div className="flex items-center gap-2">
              <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={`对 ${targetName} 说...`}
                disabled={isStreaming}
                className="
                  flex-1 px-3 py-2
                  bg-g-bg-sidebar
                  border border-g-border
                  rounded-lg
                  text-sm font-body
                  text-[var(--g-text-primary)]
                  placeholder:text-g-text-muted
                  focus:outline-none focus:border-g-gold/50
                  disabled:opacity-50
                "
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || isStreaming}
                className="
                  p-2
                  bg-g-gold/20
                  border border-g-gold/30
                  rounded-lg
                  hover:bg-g-gold/30
                  disabled:opacity-50 disabled:cursor-not-allowed
                  transition-colors
                "
              >
                <Send className="w-4 h-4 text-g-gold" />
              </button>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
};

export default PrivateChatPanel;
