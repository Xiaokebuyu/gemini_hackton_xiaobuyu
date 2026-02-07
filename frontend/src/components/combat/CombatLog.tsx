/**
 * Combat log display
 */
import React, { useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import {
  ScrollText,
  Sword,
  Zap,
  Heart,
  Sparkles,
  CircleDot,
  Footprints,
  Play,
  Pause,
  Trophy,
} from 'lucide-react';
import type { CombatLogEntry, CombatLogEntryType } from '../../types';

interface CombatLogProps {
  entries: CombatLogEntry[];
  className?: string;
}

const entryTypeStyles: Record<
  CombatLogEntryType,
  { color: string; icon: React.ReactNode }
> = {
  attack: { color: 'text-g-red', icon: <Sword className="w-3 h-3 inline" /> },
  damage: { color: 'text-g-danger-high', icon: <Zap className="w-3 h-3 inline" /> },
  heal: { color: 'text-g-danger-low', icon: <Heart className="w-3 h-3 inline" /> },
  spell: { color: 'text-g-purple', icon: <Sparkles className="w-3 h-3 inline" /> },
  status: { color: 'text-g-danger-medium', icon: <CircleDot className="w-3 h-3 inline" /> },
  movement: { color: 'text-g-cyan', icon: <Footprints className="w-3 h-3 inline" /> },
  turn_start: { color: 'text-g-gold', icon: <Play className="w-3 h-3 inline" /> },
  turn_end: { color: 'text-[var(--g-text-muted)]', icon: <Pause className="w-3 h-3 inline" /> },
  combat_start: { color: 'text-g-red', icon: <Sword className="w-3 h-3 inline" /> },
  combat_end: { color: 'text-g-gold', icon: <Trophy className="w-3 h-3 inline" /> },
};

export const CombatLog: React.FC<CombatLogProps> = ({
  entries,
  className = '',
}) => {
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries]);

  return (
    <div className={`flex flex-col h-full ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-2 p-3 border-b border-[var(--g-border-default)]">
        <ScrollText className="w-4 h-4 text-g-gold" />
        <h3 className="text-sm font-heading text-g-gold">Combat Log</h3>
      </div>

      {/* Log entries */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto g-scrollbar p-3 space-y-2"
      >
        <AnimatePresence mode="popLayout">
          {entries.map((entry) => {
            const style = entryTypeStyles[entry.type];

            return (
              <motion.div
                key={entry.id}
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                className="text-sm"
              >
                {/* Timestamp */}
                <span className="text-xs text-[var(--g-text-muted)] mr-2">
                  {entry.timestamp.toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                  })}
                </span>

                {/* Icon */}
                <span className="mr-1">{style.icon}</span>

                {/* Content */}
                <span className={style.color}>
                  {entry.type === 'turn_start' && (
                    <span className="font-medium">
                      {entry.actor}'s turn begins
                    </span>
                  )}
                  {entry.type === 'turn_end' && (
                    <span>{entry.actor} ends their turn</span>
                  )}
                  {entry.type === 'attack' && (
                    <span>
                      <span className="font-medium">{entry.actor}</span>
                      {' attacks '}
                      <span className="font-medium">{entry.target}</span>
                      {entry.roll && (
                        <span className="text-[var(--g-text-muted)]">
                          {' '}
                          (d20: {entry.roll.result}
                          {entry.roll.modifier >= 0 ? '+' : ''}
                          {entry.roll.modifier} = {entry.roll.total})
                        </span>
                      )}
                    </span>
                  )}
                  {entry.type === 'damage' && entry.result && (
                    <span>
                      <span className="font-medium">{entry.target}</span>
                      {' takes '}
                      <span className="font-bold text-g-red">
                        {entry.result.damage}
                      </span>
                      {' damage'}
                    </span>
                  )}
                  {entry.type === 'heal' && entry.result && (
                    <span>
                      <span className="font-medium">{entry.target}</span>
                      {' heals for '}
                      <span className="font-bold text-g-danger-low">
                        {entry.result.healing}
                      </span>
                      {' HP'}
                    </span>
                  )}
                  {entry.type === 'status' && entry.result && (
                    <span>
                      <span className="font-medium">{entry.target}</span>
                      {entry.result.status_applied && (
                        <span>
                          {' is now '}
                          <span className="font-bold">
                            {entry.result.status_applied}
                          </span>
                        </span>
                      )}
                      {entry.result.status_removed && (
                        <span>
                          {' is no longer '}
                          <span className="font-bold">
                            {entry.result.status_removed}
                          </span>
                        </span>
                      )}
                    </span>
                  )}
                  {entry.type === 'movement' && (
                    <span>
                      <span className="font-medium">{entry.actor}</span>
                      {' moves'}
                    </span>
                  )}
                  {entry.type === 'combat_start' && (
                    <span className="font-bold">Combat begins!</span>
                  )}
                  {entry.type === 'combat_end' && (
                    <span className="font-bold">Combat ends!</span>
                  )}
                </span>

                {/* Result message */}
                {entry.result?.message && (
                  <div className="ml-6 text-xs text-[var(--g-text-secondary)] italic">
                    {entry.result.message}
                  </div>
                )}
              </motion.div>
            );
          })}
        </AnimatePresence>

        {entries.length === 0 && (
          <div className="text-center text-[var(--g-text-muted)] text-sm py-4">
            No combat actions yet...
          </div>
        )}
      </div>
    </div>
  );
};

export default CombatLog;
