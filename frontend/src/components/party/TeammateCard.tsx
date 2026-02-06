/**
 * Teammate card component - using Sketch style
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import { MessageCircle } from 'lucide-react';
import type { PartyMember, TeammateRole } from '../../types';
import { useGameInput } from '../../api';

interface TeammateCardProps {
  member: PartyMember;
  index?: number;
  className?: string;
}

const roleConfig: Record<
  TeammateRole,
  { icon: string; color: string; labelKey: string }
> = {
  warrior: { icon: '⚔️', color: 'text-sketch-accent-red', labelKey: 'party.role.warrior' },
  healer: { icon: '💚', color: 'text-sketch-accent-green', labelKey: 'party.role.healer' },
  mage: { icon: '🔮', color: 'text-sketch-accent-purple', labelKey: 'party.role.mage' },
  rogue: { icon: '🗡️', color: 'text-sketch-accent-blue', labelKey: 'party.role.rogue' },
  support: { icon: '🛡️', color: 'text-sketch-accent-gold', labelKey: 'party.role.support' },
  scout: { icon: '👁️', color: 'text-sketch-accent-cyan', labelKey: 'party.role.scout' },
  scholar: { icon: '📚', color: 'text-sketch-ink-secondary', labelKey: 'party.role.scholar' },
};

const moodEmojis: Record<string, string> = {
  happy: '😊',
  neutral: '😐',
  sad: '😢',
  angry: '😠',
  worried: '😟',
  excited: '🤩',
  tired: '😴',
  focused: '🎯',
};

export const TeammateCard: React.FC<TeammateCardProps> = ({
  member,
  index = 0,
  className = '',
}) => {
  const { t } = useTranslation();
  const { sendInput, isLoading } = useGameInput();
  const roleInfo = roleConfig[member.role];
  const moodEmoji = moodEmojis[member.current_mood] || '😐';

  const handleTalk = () => {
    if (!isLoading) {
      sendInput(`[和${member.name}交谈]`);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.1 }}
      className={`
        bg-sketch-bg-panel
        border-2 border-sketch-ink-muted
        rounded-xl
        p-3
        shadow-parchment-sm
        hover:border-sketch-ink-secondary
        hover:shadow-parchment-md
        transition-all duration-200
        ${!member.is_active ? 'opacity-50' : ''}
        ${className}
      `}
    >
      {/* Header */}
      <div className="flex items-start gap-3 mb-2">
        {/* Avatar */}
        <div
          className={`
            w-10 h-10 rounded-full
            bg-sketch-bg-secondary
            flex items-center justify-center
            text-xl
            border ${roleInfo.color.replace('text-', 'border-')}
          `}
        >
          {roleInfo.icon}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h4 className={`font-handwritten font-medium ${roleInfo.color} truncate`}>
              {member.name}
            </h4>
            <span title={t(`party.mood.${member.current_mood}`)}>{moodEmoji}</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-sketch-ink-muted font-body">
            <span>{t(roleInfo.labelKey)}</span>
            {!member.is_active && (
              <span className="text-sketch-accent-red">({t('party.inactive')})</span>
            )}
          </div>
        </div>

        {/* Talk button */}
        <button
          onClick={handleTalk}
          disabled={!member.is_active || isLoading}
          className="
            p-2
            bg-sketch-bg-secondary
            border border-sketch-ink-muted
            hover:bg-sketch-accent-gold/20
            disabled:opacity-50 disabled:cursor-not-allowed
            transition-colors
          "
          style={{ borderRadius: '8px' }}
          title={t('party.talkTo', { name: member.name })}
        >
          <MessageCircle className="w-4 h-4 text-sketch-accent-gold" />
        </button>
      </div>

      {/* Personality snippet */}
      {member.personality && (
        <p className="text-xs text-sketch-ink-muted italic line-clamp-2 font-body">
          "{member.personality}"
        </p>
      )}

      {/* Response tendency indicator */}
      <div className="mt-2 pt-2 border-t border-sketch-ink-faint">
        <div className="flex items-center justify-between text-xs">
          <span className="text-sketch-ink-muted font-body">{t('party.chattiness')}</span>
          <div className="flex gap-0.5">
            {[1, 2, 3, 4, 5].map((i) => (
              <div
                key={i}
                className={`
                  w-2 h-2 rounded-full
                  ${
                    i <= Math.round(member.response_tendency * 5)
                      ? 'bg-sketch-accent-green'
                      : 'bg-sketch-bg-secondary'
                  }
                `}
              />
            ))}
          </div>
        </div>
      </div>
    </motion.div>
  );
};

export default TeammateCard;
