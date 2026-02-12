/**
 * Teammate card — compact row with role icon, name, mood, and actions
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
import type { PartyMember, TeammateRole, DispositionSnapshot } from '../../types';
import { useStreamGameInput } from '../../api';
import { useGameStore } from '../../stores/gameStore';
import { usePrivateChatStore } from '../../stores/privateChatStore';

interface TeammateCardProps {
  member: PartyMember;
  index?: number;
  isLast?: boolean;
  className?: string;
}

const roleConfig: Record<
  TeammateRole,
  { icon: React.ReactNode; color: string; labelKey: string }
> = {
  warrior: { icon: <Sword className="w-4 h-4" />, color: 'text-g-red', labelKey: 'party.role.warrior' },
  healer: { icon: <Heart className="w-4 h-4" />, color: 'text-g-green', labelKey: 'party.role.healer' },
  mage: { icon: <Sparkles className="w-4 h-4" />, color: 'text-g-purple', labelKey: 'party.role.mage' },
  rogue: { icon: <Crosshair className="w-4 h-4" />, color: 'text-g-blue', labelKey: 'party.role.rogue' },
  support: { icon: <Shield className="w-4 h-4" />, color: 'text-g-gold', labelKey: 'party.role.support' },
  scout: { icon: <Eye className="w-4 h-4" />, color: 'text-g-cyan', labelKey: 'party.role.scout' },
  scholar: { icon: <BookOpen className="w-4 h-4" />, color: 'text-g-text-secondary', labelKey: 'party.role.scholar' },
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
  isLast = false,
  className = '',
}) => {
  const { t } = useTranslation();
  const { sendInput, isLoading } = useStreamGameInput();
  const openChat = usePrivateChatStore((s) => s.openChat);
  const disposition = useGameStore((s) => s.dispositions[member.character_id]) as DispositionSnapshot | undefined;
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
      initial={{ opacity: 0, x: 12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: index * 0.08 }}
      className={`
        py-3
        ${!isLast ? 'border-b border-[var(--g-accent-gold)]/10' : ''}
        ${!member.is_active ? 'opacity-40' : ''}
        ${className}
      `}
    >
      {/* Main row: icon + info + actions */}
      <div className="flex items-center gap-3">
        {/* Role icon */}
        <div className={`flex-shrink-0 ${roleInfo.color}`}>
          {roleInfo.icon}
        </div>

        {/* Name + role */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className={`text-sm font-heading font-medium ${roleInfo.color} truncate`}>
              {member.name}
            </span>
            <span
              className={`w-2 h-2 rounded-full ${mood.color} flex-shrink-0`}
              title={t(`party.mood.${member.current_mood}`, mood.label)}
            />
          </div>
          <div className="flex items-center gap-2 text-[11px] text-g-text-muted mt-0.5">
            <span>{t(roleInfo.labelKey)}</span>
            {!member.is_active && (
              <span className="text-g-red">({t('party.inactive')})</span>
            )}
          </div>
          {/* Approval mini bar — 始终显示，无数据时默认 0 */}
          {(() => {
            const approval = disposition?.approval ?? 0;
            const trust = disposition?.trust ?? 0;
            const fear = disposition?.fear ?? 0;
            const romance = disposition?.romance ?? 0;
            return (
              <div
                className="flex items-center gap-1.5 mt-1"
                title={`好感 ${approval} | 信任 ${trust} | 畏惧 ${fear} | 浪漫 ${romance}`}
              >
                <div className="flex-1 h-1.5 bg-[var(--g-bg-secondary)] rounded-full overflow-hidden">
                  <motion.div
                    className="h-full rounded-full"
                    initial={false}
                    animate={{
                      width: `${Math.max(2, (approval + 100) / 200 * 100)}%`,
                    }}
                    transition={{ type: 'spring', stiffness: 300, damping: 30 }}
                    style={{
                      backgroundColor:
                        approval >= 50
                          ? 'var(--g-green, #22c55e)'
                          : approval >= 0
                            ? 'var(--g-accent-gold, #d4a017)'
                            : approval >= -50
                              ? 'var(--g-danger-medium, #f59e0b)'
                              : 'var(--g-red, #ef4444)',
                    }}
                  />
                </div>
                <span className="text-[10px] text-g-text-muted w-7 text-right tabular-nums">
                  {approval > 0 ? '+' : ''}{approval}
                </span>
              </div>
            );
          })()}
        </div>

        {/* Action buttons */}
        <div className="flex gap-1 flex-shrink-0">
          <motion.button
            onClick={handleTalk}
            disabled={!member.is_active || isLoading}
            whileHover={member.is_active && !isLoading ? { scale: 1.1 } : undefined}
            whileTap={member.is_active && !isLoading ? { scale: 0.9 } : undefined}
            className="
              p-1.5
              text-g-text-muted hover:text-[var(--g-accent-gold)]
              disabled:opacity-30 disabled:cursor-not-allowed
              transition-colors
            "
            title={t('party.talkTo', { name: member.name })}
          >
            <MessageCircle className="w-3.5 h-3.5" />
          </motion.button>
          <motion.button
            onClick={handlePrivateChat}
            disabled={!member.is_active}
            whileHover={member.is_active ? { scale: 1.1 } : undefined}
            whileTap={member.is_active ? { scale: 0.9 } : undefined}
            className="
              p-1.5
              text-g-text-muted hover:text-g-purple
              disabled:opacity-30 disabled:cursor-not-allowed
              transition-colors
            "
            title={`${member.name} 私聊`}
          >
            <MessageSquare className="w-3.5 h-3.5" />
          </motion.button>
        </div>
      </div>

      {/* Personality quote */}
      {member.personality && (
        <p className="text-[11px] text-g-text-muted/70 italic mt-2 ml-7 line-clamp-1">
          "{member.personality}"
        </p>
      )}
    </motion.div>
  );
};

export default TeammateCard;
