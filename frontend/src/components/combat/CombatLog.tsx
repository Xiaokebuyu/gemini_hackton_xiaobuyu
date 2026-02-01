/**
 * Combat log display
 */
import React, { useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ScrollText } from 'lucide-react';
import type { CombatLogEntry, CombatLogEntryType } from '../../types';

interface CombatLogProps {
  entries: CombatLogEntry[];
  className?: string;
}

const entryTypeStyles: Record<
  CombatLogEntryType,
  { color: string; icon: string }
> = {
  attack: { color: 'text-accent-red', icon: '⚔️' },
  damage: { color: 'text-danger-high', icon: '💥' },
  heal: { color: 'text-danger-low', icon: '💚' },
  spell: { color: 'text-accent-purple', icon: '✨' },
  status: { color: 'text-danger-medium', icon: '🔮' },
  movement: { color: 'text-accent-cyan', icon: '👣' },
  turn_start: { color: 'text-accent-gold', icon: '▶️' },
  turn_end: { color: 'text-[var(--color-text-muted)]', icon: '⏸️' },
  combat_start: { color: 'text-accent-red', icon: '⚔️' },
  combat_end: { color: 'text-accent-gold', icon: '🏆' },
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
      <div className="flex items-center gap-2 p-3 border-b border-[var(--color-border-secondary)]">
        <ScrollText className="w-4 h-4 text-accent-gold" />
        <h3 className="text-sm font-fantasy text-accent-gold">Combat Log</h3>
      </div>

      {/* Log entries */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto fantasy-scrollbar p-3 space-y-2"
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
                <span className="text-xs text-[var(--color-text-muted)] mr-2">
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
                        <span className="text-[var(--color-text-muted)]">
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
                      <span className="font-bold text-accent-red">
                        {entry.result.damage}
                      </span>
                      {' damage'}
                    </span>
                  )}
                  {entry.type === 'heal' && entry.result && (
                    <span>
                      <span className="font-medium">{entry.target}</span>
                      {' heals for '}
                      <span className="font-bold text-danger-low">
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
                  <div className="ml-6 text-xs text-[var(--color-text-secondary)] italic">
                    {entry.result.message}
                  </div>
                )}
              </motion.div>
            );
          })}
        </AnimatePresence>

        {entries.length === 0 && (
          <div className="text-center text-[var(--color-text-muted)] text-sm py-4">
            No combat actions yet...
          </div>
        )}
      </div>
    </div>
  );
};

export default CombatLog;
