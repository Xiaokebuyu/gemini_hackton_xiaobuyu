/**
 * Party panel — teammate list with minimal separators
 */
import React from 'react';
import { useTranslation } from 'react-i18next';
import { Users, UserPlus, Footprints, User, Eye, Lock } from 'lucide-react';
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
    <div className={`flex flex-col ${className}`}>
      {/* Section header */}
      <div className="flex items-center justify-between px-5 py-3">
        <div className="flex items-center gap-2">
          <Users className="w-3.5 h-3.5 text-[var(--g-accent-gold)]" />
          <h3 className="text-xs font-heading text-[var(--g-accent-gold)] tracking-wide uppercase">
            {t('party.title')}
          </h3>
        </div>
        {currentParty && (
          <span className="text-[11px] text-g-text-muted tabular-nums">
            {activeMembers.length} / {currentParty.max_size}
          </span>
        )}
      </div>

      {/* Content */}
      <div className="px-5 pb-4">
        {!currentParty || activeMembers.length === 0 ? (
          <div className="flex flex-col items-center py-10 text-center">
            <UserPlus className="w-8 h-8 text-g-text-muted/40 mb-3" />
            <p className="text-xs text-g-text-muted">
              {t('party.empty')}
            </p>
            <p className="text-[11px] text-g-text-muted/60 mt-1">
              {t('party.addMember')}
            </p>
          </div>
        ) : (
          <div>
            {activeMembers.map((member, index) => (
              <TeammateCard
                key={member.character_id}
                member={member}
                index={index}
                isLast={index === activeMembers.length - 1 && inactiveMembers.length === 0}
              />
            ))}

            {/* Inactive members */}
            {inactiveMembers.length > 0 && (
              <>
                <div className="flex items-center gap-3 my-3">
                  <div className="flex-1 h-px bg-g-text-muted/15" />
                  <span className="text-[10px] text-g-text-muted/60 uppercase tracking-wider">
                    {t('party.inactive')}
                  </span>
                  <div className="flex-1 h-px bg-g-text-muted/15" />
                </div>
                {inactiveMembers.map((member, index) => (
                  <TeammateCard
                    key={member.character_id}
                    member={member}
                    index={activeMembers.length + index}
                    isLast={index === inactiveMembers.length - 1}
                  />
                ))}
              </>
            )}
          </div>
        )}
      </div>

      {/* Party info footer */}
      {currentParty && (
        <div className="px-5 py-2.5 border-t border-[var(--g-accent-gold)]/10">
          <div className="flex items-center gap-3 text-[10px] text-g-text-muted/70">
            <span className="inline-flex items-center gap-1">
              {currentParty.auto_follow ? (
                <><Footprints className="w-3 h-3" /> Following</>
              ) : (
                <><User className="w-3 h-3" /> Independent</>
              )}
            </span>
            <span className="text-g-text-muted/30">|</span>
            <span className="inline-flex items-center gap-1">
              {currentParty.share_events ? (
                <><Eye className="w-3 h-3" /> Shared</>
              ) : (
                <><Lock className="w-3 h-3" /> Private</>
              )}
            </span>
          </div>
        </div>
      )}
    </div>
  );
};

export default PartyPanel;
