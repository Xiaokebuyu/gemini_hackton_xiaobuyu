/**
 * Main Game Layout - Left panel + Center + Right overlay panel
 * Uses golden theme
 */
import React, { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { RotateCcw, Backpack } from 'lucide-react';
import { useGameStore, useCombatStore, useChatStore, useUIStore, toast } from '../../stores';
import LeftPanel from './LeftPanel';
import CenterPanel from './CenterPanel';
import RightPanel from './RightPanel';
import CombatOverlay from '../combat/CombatOverlay';
import { DiceRollDisplay } from '../dice';
import PrivateChatPanel from '../party/PrivateChatPanel';
import { LanguageSwitch } from '../shared';

interface GameLayoutProps {
  children?: React.ReactNode;
}

export const GameLayout: React.FC<GameLayoutProps> = () => {
  const { t } = useTranslation();
  const { combatId, clearSession, pendingDiceRoll, setDiceRoll } = useGameStore();
  const { isActive: isCombatActive, clearCombat } = useCombatStore();
  const { clearMessages } = useChatStore();
  const { rightPanelCollapsed, toggleRightPanel } = useUIStore();

  const handleDiceComplete = useCallback(() => setDiceRoll(null), [setDiceRoll]);

  const handleReselectSession = () => {
    clearCombat();
    clearMessages();
    clearSession();
    toast.info('已返回会话选择');
  };

  return (
    <div className="h-screen w-screen overflow-hidden bg-g-bg-base">
      {/* Top nav bar */}
      <div className="h-12 bg-g-bg-surface border-b border-g-border flex items-center justify-end px-4 gap-2 z-50">
        <button
          onClick={handleReselectSession}
          className="
            px-3 py-1.5
            rounded-lg
            border border-g-border
            bg-g-bg-surface
            text-g-text-primary
            text-xs font-body
            hover:border-g-gold
            transition-colors
            flex items-center gap-1.5
          "
          title="返回并重新选择对话"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          <span>重新选择对话</span>
        </button>

        {/* Open right panel button — always visible, gold accent */}
        <button
          onClick={toggleRightPanel}
          className={`
            px-3 py-1.5
            rounded-lg
            border
            text-xs font-body
            transition-all duration-200
            flex items-center gap-1.5
            ${rightPanelCollapsed
              ? 'border-g-gold/40 bg-g-gold/5 text-g-gold hover:bg-g-gold/15 hover:border-g-gold hover:shadow-[0_0_8px_rgba(196,154,42,0.2)]'
              : 'border-g-gold bg-g-gold/10 text-g-gold shadow-[0_0_8px_rgba(196,154,42,0.15)]'
            }
          `}
        >
          <Backpack className="w-4 h-4" />
          <span>{t('ui.rightPanel')}</span>
        </button>

        {/* Language switch */}
        <LanguageSwitch />
      </div>

      {/* Main content */}
      <div className="h-[calc(100vh-3rem)] flex">
        {/* Left Panel - Navigation */}
        <LeftPanel className="flex-shrink-0 h-full" />

        {/* Center Panel - Narrative */}
        <CenterPanel className="flex-1 min-w-0 h-full p-3" />
      </div>

      {/* Right Panel - Overlay */}
      <RightPanel />

      {/* Combat Overlay */}
      {combatId && isCombatActive && <CombatOverlay />}

      {/* Global dice animation (combat + ability checks) */}
      {pendingDiceRoll && (
        <DiceRollDisplay
          roll={pendingDiceRoll}
          onComplete={handleDiceComplete}
        />
      )}

      {/* Private Chat Overlay */}
      <PrivateChatPanel />
    </div>
  );
};

export default GameLayout;
