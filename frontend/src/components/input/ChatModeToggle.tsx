/**
 * Think/Say mode toggle component
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import { Brain, MessageCircle } from 'lucide-react';
import { useGameStore } from '../../stores';

interface ChatModeToggleProps {
  className?: string;
}

export const ChatModeToggle: React.FC<ChatModeToggleProps> = ({
  className = '',
}) => {
  const { t } = useTranslation();
  const { chatMode, setChatMode } = useGameStore();

  return (
    <div className={`flex items-center gap-1 ${className}`}>
      {/* Think mode */}
      <motion.button
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        onClick={() => setChatMode('think')}
        className={`
          flex items-center gap-2
          px-3 py-2
          border-2
          transition-all duration-200
          font-handwritten
          ${
            chatMode === 'think'
              ? 'bg-sketch-accent-purple/20 border-sketch-accent-purple text-sketch-accent-purple'
              : 'bg-sketch-bg-panel border-sketch-ink-faint text-sketch-ink-secondary hover:border-sketch-accent-purple/50'
          }
        `}
        style={{ borderRadius: '4px 0 0 4px' }}
        title={t('chat.modeHint.think')}
      >
        <Brain className="w-4 h-4" />
        <span className="text-sm font-medium">{t('chat.modeThink')}</span>
      </motion.button>

      {/* Say mode */}
      <motion.button
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        onClick={() => setChatMode('say')}
        className={`
          flex items-center gap-2
          px-3 py-2
          border-2
          transition-all duration-200
          font-handwritten
          ${
            chatMode === 'say'
              ? 'bg-sketch-accent-green/20 border-sketch-accent-green text-sketch-accent-green'
              : 'bg-sketch-bg-panel border-sketch-ink-faint text-sketch-ink-secondary hover:border-sketch-accent-green/50'
          }
        `}
        style={{ borderRadius: '0 4px 4px 0' }}
        title={t('chat.modeHint.say')}
      >
        <MessageCircle className="w-4 h-4" />
        <span className="text-sm font-medium">{t('chat.modeSay')}</span>
      </motion.button>
    </div>
  );
};

export default ChatModeToggle;
