/**
 * Party panel component
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
    <div className={`h-full flex flex-col ${className}`}>
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b-2 border-g-border">
        <div className="flex items-center gap-2">
          <Users className="w-4 h-4 text-g-gold" />
          <h3 className="text-sm font-heading text-g-gold">{t('party.title')}</h3>
        </div>
        {currentParty && (
          <span className="text-xs g-text-muted font-body">
            {activeMembers.length}/{currentParty.max_size}
          </span>
        )}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto g-scrollbar p-3">
        {!currentParty || activeMembers.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center py-8">
            <div
              className="
                w-16 h-16 rounded-full
                bg-g-bg-sidebar
                flex items-center justify-center
                mb-4
              "
            >
              <UserPlus className="w-8 h-8 g-text-muted" />
            </div>
            <h4 className="text-sm font-medium g-text-secondary mb-1 font-body">
              {t('party.empty')}
            </h4>
            <p className="text-xs g-text-muted max-w-[200px] font-body">
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
                  <div className="flex-1 h-px bg-g-border" />
                  <span className="text-xs g-text-muted font-body">
                    {t('party.inactive')}
                  </span>
                  <div className="flex-1 h-px bg-g-border" />
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
        <div className="px-3 py-2 border-t border-g-border">
          <div className="flex items-center gap-2 text-[10px] g-text-muted font-body">
            <span className="flex items-center gap-1">
              {currentParty.auto_follow ? (
                <><Footprints className="w-3 h-3" /> Following</>
              ) : (
                <><User className="w-3 h-3" /> Independent</>
              )}
            </span>
            <span>·</span>
            <span className="flex items-center gap-1">
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
