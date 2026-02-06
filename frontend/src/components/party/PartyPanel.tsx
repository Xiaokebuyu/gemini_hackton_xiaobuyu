/**
 * Party panel component
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Users, UserPlus } from 'lucide-react';
import { useGameStore } from '../../stores';
import TeammateCard from './TeammateCard';
import type { Party } from '../../types';

interface PartyPanelProps {
  party?: Party;
  className?: string;
}

export const PartyPanel: React.FC<PartyPanelProps> = ({
  party,
  className = '',
}) => {
  const { t } = useTranslation();
  const { party: storeParty } = useGameStore();
  const currentParty = party || storeParty;

  const activeMembers = currentParty?.members.filter((m) => m.is_active) || [];
  const inactiveMembers = currentParty?.members.filter((m) => !m.is_active) || [];

  return (
    <div className={`h-full flex flex-col ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b-2 border-sketch-ink-muted">
        <div className="flex items-center gap-2">
          <Users className="w-4 h-4 text-sketch-accent-gold" />
          <h3 className="text-sm font-fantasy text-sketch-accent-gold">{t('party.title')}</h3>
        </div>
        {currentParty && (
          <span className="text-xs text-sketch-ink-muted font-body">
            {activeMembers.length}/{currentParty.max_size}
          </span>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto sketch-scrollbar p-3">
        {!currentParty || activeMembers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center py-8">
            <div
              className="
                w-16 h-16 rounded-full
                bg-sketch-bg-secondary
                flex items-center justify-center
                mb-4
              "
            >
              <UserPlus className="w-8 h-8 text-sketch-ink-muted" />
            </div>
            <h4 className="text-sm font-medium text-sketch-ink-secondary mb-1 font-body">
              {t('party.empty')}
            </h4>
            <p className="text-xs text-sketch-ink-muted max-w-[200px] font-body">
              {t('party.addMember')}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Active members */}
            {activeMembers.map((member, index) => (
              <TeammateCard
                key={member.character_id}
                member={member}
                index={index}
              />
            ))}

            {/* Inactive members */}
            {inactiveMembers.length > 0 && (
              <>
                <div className="flex items-center gap-2 mt-4">
                  <div className="flex-1 h-px bg-sketch-ink-faint" />
                  <span className="text-xs text-sketch-ink-muted font-body">
                    {t('party.inactive')}
                  </span>
                  <div className="flex-1 h-px bg-sketch-ink-faint" />
                </div>
                {inactiveMembers.map((member, index) => (
                  <TeammateCard
                    key={member.character_id}
                    member={member}
                    index={activeMembers.length + index}
                  />
                ))}
              </>
            )}
          </div>
        )}
      </div>

      {/* Party info (read-only) */}
      {currentParty && (
        <div className="px-3 py-2 border-t border-sketch-ink-faint">
          <div className="flex items-center gap-2 text-[10px] text-sketch-ink-faint font-body">
            <span>
              {currentParty.auto_follow ? '👣 Following' : '🚶 Independent'}
            </span>
            <span>·</span>
            <span>
              {currentParty.share_events ? '👁️ Shared' : '🔒 Private'}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default PartyPanel;
