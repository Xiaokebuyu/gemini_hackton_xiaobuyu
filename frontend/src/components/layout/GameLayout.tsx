/**
 * Main Game Layout - Three Column Design
 * Uses Sketch (hand-drawn) theme exclusively
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
    <div className="h-screen w-screen overflow-hidden sketch-theme bg-sketch-bg-primary">
      {/* Paper texture for sketch theme */}
      <div className="absolute inset-0 sketch-paper-texture pointer-events-none" />
      {/* Vignette effect */}
      <div
        className="absolute inset-0 pointer-events-none z-[1]"
        style={{ boxShadow: 'inset 0 0 120px rgba(30,20,10,0.25)' }}
      />

      {/* Top bar with settings */}
      <div className="absolute top-2 right-2 z-50 flex items-center gap-2">
        <button
          onClick={handleReselectSession}
          className="
            px-3 py-1.5
            rounded-lg
            border border-sketch-ink-secondary
            bg-sketch-bg-panel
            text-sketch-ink-primary
            text-xs font-body
            hover:border-sketch-accent-gold
            transition-colors
            flex items-center gap-1.5
          "
          title="返回并重新选择对话"
        >
          <RotateCcw className="w-3.5 h-3.5" />
          <span>重新选择对话</span>
        </button>
        {/* Language switch */}
        <LanguageSwitch variant="sketch" />
      </div>

      {/* Main content */}
      <div className="relative z-10 h-full flex">
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
