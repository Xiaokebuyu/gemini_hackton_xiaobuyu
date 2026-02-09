/**
 * Teammate card component - Golden D&D theme
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { motion } from 'framer-motion';
import {
  MessageCircle,
  MessageSquare,
  Sword,
  Heart,
  Sparkles,
  Crosshair,
  Shield,
  Eye,
  BookOpen,
} from 'lucide-react';
import type { PartyMember, TeammateRole } from '../../types';
import { useStreamGameInput } from '../../api';
import { usePrivateChatStore } from '../../stores/privateChatStore';

interface TeammateCardProps {
  member: PartyMember;
  index?: number;
  className?: string;
}

const roleConfig: Record<
  TeammateRole,
  { icon: React.ReactNode; color: string; labelKey: string }
> = {
  warrior: { icon: <Sword className="w-5 h-5" />, color: 'text-g-red', labelKey: 'party.role.warrior' },
  healer: { icon: <Heart className="w-5 h-5" />, color: 'text-g-green', labelKey: 'party.role.healer' },
  mage: { icon: <Sparkles className="w-5 h-5" />, color: 'text-g-purple', labelKey: 'party.role.mage' },
  rogue: { icon: <Crosshair className="w-5 h-5" />, color: 'text-g-blue', labelKey: 'party.role.rogue' },
  support: { icon: <Shield className="w-5 h-5" />, color: 'text-g-gold', labelKey: 'party.role.support' },
  scout: { icon: <Eye className="w-5 h-5" />, color: 'text-g-cyan', labelKey: 'party.role.scout' },
  scholar: { icon: <BookOpen className="w-5 h-5" />, color: 'text-g-text-secondary', labelKey: 'party.role.scholar' },
};

const moodConfig: Record<string, { color: string; label: string }> = {
  happy: { color: 'bg-g-green', label: 'Happy' },
  neutral: { color: 'bg-g-text-muted', label: 'Neutral' },
  sad: { color: 'bg-g-blue', label: 'Sad' },
  angry: { color: 'bg-g-red', label: 'Angry' },
  worried: { color: 'bg-g-danger-medium', label: 'Worried' },
  excited: { color: 'bg-g-gold', label: 'Excited' },
  tired: { color: 'bg-g-purple', label: 'Tired' },
  focused: { color: 'bg-g-cyan', label: 'Focused' },
};

export const TeammateCard: React.FC<TeammateCardProps> = ({
  member,
  index = 0,
  className = '',
}) => {
  const { t } = useTranslation();
  const { sendInput, isLoading } = useStreamGameInput();
  const openChat = usePrivateChatStore((s) => s.openChat);
  const roleInfo = roleConfig[member.role];
  const mood = moodConfig[member.current_mood] || moodConfig.neutral;

  const handleTalk = () => {
    if (!isLoading) {
      sendInput(`[和${member.name}交谈]`);
    }
  };

  const handlePrivateChat = () => {
    openChat(member.character_id, member.name);
  };

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.1 }}
      className={`
        bg-g-bg-surface
        border-2 border-g-border
        rounded-xl
        p-3
        shadow-g-sm
        hover:border-g-border-strong
        hover:shadow-g-md
        transition-all duration-200
        card-hover
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
            bg-g-bg-sidebar
            flex items-center justify-center
            border ${roleInfo.color.replace('text-', 'border-')}
          `}
        >
          {roleInfo.icon}
        </div>

        {/* Info */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h4 className={`font-heading font-medium ${roleInfo.color} truncate`}>
              {member.name}
            </h4>
            <span
              className={`w-2.5 h-2.5 rounded-full ${mood.color} inline-block`}
              title={t(`party.mood.${member.current_mood}`, mood.label)}
            />
          </div>
          <div className="flex items-center gap-2 text-xs text-g-text-muted font-body">
            <span>{t(roleInfo.labelKey)}</span>
            {!member.is_active && (
              <span className="text-g-red">({t('party.inactive')})</span>
            )}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex gap-1">
          <motion.button
            onClick={handleTalk}
            disabled={!member.is_active || isLoading}
            whileHover={member.is_active && !isLoading ? { scale: 1.05 } : undefined}
            whileTap={member.is_active && !isLoading ? { scale: 0.95 } : undefined}
            className="
              p-2
              bg-g-bg-sidebar
              border border-g-border
              hover:bg-g-gold/20
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors
            "
            style={{ borderRadius: '8px' }}
            title={t('party.talkTo', { name: member.name })}
          >
            <MessageCircle className="w-4 h-4 text-g-gold" />
          </motion.button>
          <motion.button
            onClick={handlePrivateChat}
            disabled={!member.is_active}
            whileHover={member.is_active ? { scale: 1.05 } : undefined}
            whileTap={member.is_active ? { scale: 0.95 } : undefined}
            className="
              p-2
              bg-g-bg-sidebar
              border border-g-border
              hover:bg-g-purple/20
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-colors
            "
            style={{ borderRadius: '8px' }}
            title={`${member.name} 私聊`}
          >
            <MessageSquare className="w-4 h-4 text-g-purple" />
          </motion.button>
        </div>
      </div>

      {/* Personality snippet */}
      {member.personality && (
        <p className="text-xs text-g-text-muted italic line-clamp-2 font-body bg-g-bg-surface-alt rounded-lg px-2 py-1.5">
          "{member.personality}"
        </p>
      )}

      {/* Response tendency indicator */}
      <div className="mt-2 pt-2 border-t border-g-border">
        <div className="flex items-center justify-between text-xs">
          <span className="text-g-text-muted font-body">{t('party.chattiness')}</span>
          <div className="flex gap-0.5">
            {[1, 2, 3, 4, 5].map((i) => (
              <div
                key={i}
                className={`
                  w-2.5 h-2.5 rounded-full
                  ${
                    i <= Math.round(member.response_tendency * 5)
                      ? 'bg-g-green'
                      : 'bg-g-bg-sidebar'
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
