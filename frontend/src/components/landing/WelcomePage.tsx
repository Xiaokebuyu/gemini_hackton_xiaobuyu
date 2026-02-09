/**
 * Welcome page - RPG title screen style entry point
 */
import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitch } from '../shared';
import SessionCreator from './SessionCreator';

import type { CreateGameSessionResponse } from '../../types';

type TitleMode = 'menu' | 'new-adventure' | 'continue-journey';

interface WelcomePageProps {
  onSessionCreated: (worldId: string, sessionId: string, createResponse?: CreateGameSessionResponse) => void;
}

/* ── Inline sub-components ── */

const OrnamentDivider: React.FC = () => (
  <div className="flex items-center justify-center gap-3 animate-ornament-reveal">
    <div className="h-px w-24 sm:w-36 bg-gradient-to-r from-transparent via-[var(--g-accent-gold)]/40 to-[var(--g-accent-gold)]" />
    <span className="text-[var(--g-accent-gold)] text-sm opacity-70 select-none">✦</span>
    <div className="h-px w-24 sm:w-36 bg-gradient-to-l from-transparent via-[var(--g-accent-gold)]/40 to-[var(--g-accent-gold)]" />
  </div>
);

const TitleMenuButton: React.FC<{
  title: string;
  description: string;
  onClick: () => void;
  delay?: number;
}> = ({ title, description, onClick, delay = 0 }) => (
  <motion.button
    initial={{ opacity: 0, x: -20 }}
    animate={{ opacity: 1, x: 0 }}
    transition={{ delay, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
    whileHover={{ x: 6 }}
    whileTap={{ scale: 0.98 }}
    onClick={onClick}
    className="
      group w-full text-left
      flex items-center gap-3
      px-4 py-3
      rounded-lg
      transition-colors duration-200
      hover:bg-[rgba(196,154,42,0.1)]
    "
  >
    {/* Gold left bar */}
    <div className="
      w-1.5 self-stretch rounded-full
      bg-[rgba(196,154,42,0.4)]
      group-hover:bg-[var(--g-accent-gold)]
      transition-colors duration-200
    " />
    <div className="flex-1">
      <div className="
        font-heading text-lg
        text-[var(--g-title-text-primary)]
        group-hover:text-[var(--g-accent-gold)]
        transition-colors duration-200
      ">
        {title}
      </div>
      <div className="font-body text-sm text-[var(--g-title-text-muted)] mt-0.5">
        {description}
      </div>
    </div>
    {/* Right arrow — visible only on hover */}
    <ChevronRight className="
      w-4 h-4 text-[var(--g-accent-gold)]
      opacity-0 group-hover:opacity-100
      transition-opacity duration-200
      flex-shrink-0
    " />
  </motion.button>
);

const BackToMenuButton: React.FC<{ onClick: () => void; label: string }> = ({ onClick, label }) => (
  <motion.button
    initial={{ opacity: 0 }}
    animate={{ opacity: 1 }}
    transition={{ delay: 0.2 }}
    onClick={onClick}
    className="
      flex items-center gap-1
      font-body text-sm
      text-[var(--g-title-text-muted)]
      hover:text-[var(--g-accent-gold)]
      transition-colors duration-200
      mb-6
    "
  >
    <ChevronLeft className="w-4 h-4" />
    {label}
  </motion.button>
);

const TitleMenu: React.FC<{ onSelect: (mode: TitleMode) => void }> = ({ onSelect }) => {
  const { t } = useTranslation();

  return (
    <motion.div
      key="title-menu"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.4 }}
      className="text-center flex flex-col items-center"
    >
      {/* Top ornament */}
      <OrnamentDivider />

      {/* Title */}
      <motion.h1
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2, duration: 0.6 }}
        className="
          font-heading text-4xl sm:text-5xl tracking-[0.18em]
          bg-gradient-to-b from-[#E8C44A] to-[#C49A2A]
          bg-clip-text text-transparent
          animate-title-shimmer
          mt-8 mb-2
          select-none
        "
      >
        CHRONICLE
      </motion.h1>
      <motion.p
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 0.4, duration: 0.5 }}
        className="font-body text-sm text-[var(--g-title-text-muted)] mb-8"
      >
        AI-Driven Interactive RPG
      </motion.p>

      {/* Bottom ornament */}
      <OrnamentDivider />

      {/* Menu buttons */}
      <div className="mt-8 w-full max-w-xs space-y-2">
        <TitleMenuButton
          title={t('landing.newAdventure')}
          description={t('landing.newAdventureDesc')}
          onClick={() => onSelect('new-adventure')}
          delay={0.5}
        />
        <TitleMenuButton
          title={t('landing.continueJourney')}
          description={t('landing.continueJourneyDesc')}
          onClick={() => onSelect('continue-journey')}
          delay={0.65}
        />
      </div>
    </motion.div>
  );
};

/* ── Main component ── */

export const WelcomePage: React.FC<WelcomePageProps> = ({ onSessionCreated }) => {
  const { t } = useTranslation();
  const [mode, setMode] = useState<TitleMode>('menu');

  return (
    <div className="h-screen w-screen overflow-y-auto title-screen-bg relative">
      {/* Language switch — dark overlay style */}
      <div className="absolute top-4 right-4 z-50">
        <LanguageSwitch className="!bg-transparent !border-[var(--g-title-border)] !text-[var(--g-title-text-muted)] hover:!text-[var(--g-accent-gold)] hover:!bg-[rgba(196,154,42,0.06)]" />
      </div>

      {/* Center content */}
      <div className="relative z-10 min-h-full flex flex-col items-center justify-center py-8 px-4">
        <AnimatePresence mode="wait">
          {mode === 'menu' ? (
            <TitleMenu key="menu" onSelect={setMode} />
          ) : (
            <motion.div
              key="creator"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -20 }}
              transition={{ duration: 0.4 }}
              className="w-full max-w-md flex flex-col items-start"
            >
              <BackToMenuButton onClick={() => setMode('menu')} label={t('landing.back')} />
              <SessionCreator
                mode={mode === 'new-adventure' ? 'new' : 'continue'}
                onSessionCreated={onSessionCreated}
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Footer */}
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.8 }}
          className="absolute bottom-6 flex flex-col items-center gap-2"
        >
          <div className="h-px w-12 bg-gradient-to-r from-transparent via-[var(--g-accent-gold)]/30 to-transparent" />
          <span className="text-xs text-[var(--g-title-text-muted)] font-body animate-breathe-opacity">
            Powered by Gemini + MCP
          </span>
        </motion.div>
      </div>
    </div>
  );
};

export default WelcomePage;
