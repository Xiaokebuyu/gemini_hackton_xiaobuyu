/**
 * Main Game Layout - Three Column Design
 * Uses Sketch (hand-drawn) theme exclusively
 */
import React from 'react';
import { useGameStore, useCombatStore } from '../../stores';
import LeftPanel from './LeftPanel';
import CenterPanel from './CenterPanel';
import RightPanel from './RightPanel';
import CombatOverlay from '../combat/CombatOverlay';
import { LanguageSwitch } from '../shared';

interface GameLayoutProps {
  children?: React.ReactNode;
}

export const GameLayout: React.FC<GameLayoutProps> = () => {
  const { combatId } = useGameStore();
  const { isActive: isCombatActive } = useCombatStore();

  return (
    <div className="h-screen w-screen overflow-hidden sketch-theme bg-sketch-bg-primary">
      {/* Paper texture for sketch theme */}
      <div className="absolute inset-0 sketch-paper-texture pointer-events-none" />

      {/* Top bar with settings */}
      <div className="absolute top-2 right-2 z-50 flex items-center gap-2">
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
