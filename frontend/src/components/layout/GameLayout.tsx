/**
 * Main Game Layout - Three Column Design
 * Uses golden theme
 */
import React from 'react';
import { RotateCcw } from 'lucide-react';
import { useGameStore, useCombatStore, useChatStore, toast } from '../../stores';
import LeftPanel from './LeftPanel';
import CenterPanel from './CenterPanel';
import RightPanel from './RightPanel';
import CombatOverlay from '../combat/CombatOverlay';
import { LanguageSwitch } from '../shared';

interface GameLayoutProps {
  children?: React.ReactNode;
}

export const GameLayout: React.FC<GameLayoutProps> = () => {
  const { combatId, clearSession } = useGameStore();
  const { isActive: isCombatActive, clearCombat } = useCombatStore();
  const { clearMessages } = useChatStore();

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
        {/* Language switch */}
        <LanguageSwitch />
      </div>

      {/* Main content */}
      <div className="h-[calc(100vh-3rem)] flex">
        {/* Left Panel - Navigation */}
        <LeftPanel className="flex-shrink-0 h-full" />

        {/* Center Panel - Narrative */}
        <CenterPanel className="flex-1 min-w-0 h-full p-3" />

        {/* Right Panel - Status & Party */}
        <RightPanel className="flex-shrink-0 h-full" />
      </div>

      {/* Combat Overlay */}
      {combatId && isCombatActive && <CombatOverlay />}
    </div>
  );
};

export default GameLayout;
