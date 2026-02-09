/**
 * Right Panel - Overlay half-screen panel with 4 tabs (Party/Inventory/Quest/History)
 * Slides in from the right, covers 50% of the screen width.
 */
import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { motion, AnimatePresence } from 'framer-motion';
import { X } from 'lucide-react';
import { useUIStore } from '../../stores';
import { useParty } from '../../api/hooks/useParty';
import PlayerStatus from '../party/PlayerStatus';
import PartyPanel from '../party/PartyPanel';
import InventoryPanel from '../inventory/InventoryPanel';
import QuestPanel from '../quest/QuestPanel';
import HistoryPanel from '../history/HistoryPanel';

type RightTab = 'status' | 'party' | 'inventory' | 'quest' | 'history';

export const RightPanel: React.FC = () => {
  const { t } = useTranslation();
  const { rightPanelCollapsed, toggleRightPanel } = useUIStore();
  useParty();
  const [activeTab, setActiveTab] = useState<RightTab>('status');

  const isOpen = !rightPanelCollapsed;

  const tabs: { key: RightTab; label: string }[] = [
    { key: 'status', label: t('tabs.status', '状态') },
    { key: 'party', label: t('tabs.party', '队伍') },
    { key: 'inventory', label: t('tabs.inventory', '背包') },
    { key: 'quest', label: t('tabs.quest', '任务') },
    { key: 'history', label: t('tabs.history', '记录') },
  ];

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop overlay */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 top-12 bg-black/30 z-40"
            onClick={toggleRightPanel}
          />

          {/* Slide-in panel */}
          <motion.div
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="
              fixed top-12 right-0 bottom-0 w-1/2
              bg-[var(--g-bg-base)]
              border-l border-[var(--g-accent-gold)]/20
              z-50
              flex flex-col
            "
          >
            {/* Header */}
            <div className="flex-shrink-0 px-5 pt-4 pb-0">
              <div className="flex items-center justify-between mb-4">
                <h2 className="font-heading text-base text-[var(--g-accent-gold)] tracking-wider uppercase">
                  {t('ui.rightPanel')}
                </h2>
                <button
                  onClick={toggleRightPanel}
                  className="
                    w-7 h-7
                    flex items-center justify-center
                    text-g-text-muted hover:text-[var(--g-accent-gold)]
                    transition-colors
                  "
                  aria-label="Close panel"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {/* Tab bar */}
              <div className="flex gap-6 border-b border-[var(--g-accent-gold)]/15">
                {tabs.map((tab) => (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    className={`
                      text-xs pb-2.5 relative transition-colors tracking-wide
                      ${activeTab === tab.key
                        ? 'text-[var(--g-accent-gold)] font-semibold'
                        : 'text-g-text-muted hover:text-g-text-secondary'
                      }
                    `}
                  >
                    {tab.label}
                    {activeTab === tab.key && (
                      <motion.div
                        layoutId="tab-indicator"
                        className="absolute bottom-0 left-0 right-0 h-[2px] bg-[var(--g-accent-gold)]"
                        transition={{ type: 'spring', stiffness: 400, damping: 30 }}
                      />
                    )}
                  </button>
                ))}
              </div>
            </div>

            {/* Tab content */}
            <div className="flex-1 min-h-0 overflow-y-auto g-scrollbar">
              <AnimatePresence mode="wait">
                <motion.div
                  key={activeTab}
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.15 }}
                  className="h-full"
                >
                  {activeTab === 'status' && <PlayerStatus />}
                  {activeTab === 'party' && <PartyPanel />}
                  {activeTab === 'inventory' && <InventoryPanel />}
                  {activeTab === 'quest' && <QuestPanel />}
                  {activeTab === 'history' && <HistoryPanel />}
                </motion.div>
              </AnimatePresence>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
};

export default RightPanel;
