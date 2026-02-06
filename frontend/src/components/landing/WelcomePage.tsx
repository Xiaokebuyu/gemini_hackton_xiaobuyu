/**
 * Welcome page - entry point for new sessions
 */
import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Compass } from 'lucide-react';
import { LanguageSwitch } from '../shared';
import SessionCreator from './SessionCreator';

interface WelcomePageProps {
  onSessionCreated: (worldId: string, sessionId: string) => void;
}

export const WelcomePage: React.FC<WelcomePageProps> = ({ onSessionCreated }) => {
  const [showCreator, setShowCreator] = useState(false);

  return (
    <div className="h-screen w-screen overflow-hidden sketch-theme bg-sketch-bg-primary relative">
      {/* Paper texture */}
      <div className="absolute inset-0 sketch-paper-texture pointer-events-none" />
      {/* Vignette */}
      <div
        className="absolute inset-0 pointer-events-none z-[1]"
        style={{ boxShadow: 'inset 0 0 120px rgba(44,36,22,0.15)' }}
      />

      {/* Language switch */}
      <div className="absolute top-4 right-4 z-50">
        <LanguageSwitch variant="sketch" />
      </div>

      {/* Center content */}
      <div className="relative z-10 h-full flex flex-col items-center justify-center">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="text-center"
        >
          {/* Icon */}
          <motion.div
            initial={{ scale: 0.8 }}
            animate={{ scale: 1 }}
            transition={{ delay: 0.2, duration: 0.5 }}
            className="
              w-20 h-20 mx-auto mb-6
              bg-gradient-to-b from-[#d4ad2e] to-[#c9a227]
              rounded-full
              flex items-center justify-center
              shadow-parchment-glow-gold
            "
          >
            <Compass className="w-10 h-10 text-sketch-bg-primary" />
          </motion.div>

          {/* Title */}
          <h1 className="font-fantasy text-4xl text-sketch-ink-primary mb-2">
            Chronicle
          </h1>
          <p className="font-body text-sketch-ink-muted mb-8 text-sm">
            AI-Driven Interactive RPG
          </p>

          {/* Start button or Session creator */}
          {!showCreator ? (
            <motion.button
              whileHover={{ scale: 1.03, y: -2 }}
              whileTap={{ scale: 0.98 }}
              onClick={() => setShowCreator(true)}
              className="
                px-8 py-3
                bg-gradient-to-b from-[#d4ad2e] to-[#c9a227]
                text-sketch-ink-primary
                font-fantasy text-lg
                rounded-lg
                border border-sketch-accent-gold
                shadow-parchment-md
                hover:shadow-parchment-glow-gold
                transition-shadow duration-200
              "
            >
              Begin Adventure
            </motion.button>
          ) : (
            <motion.div
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3 }}
            >
              <SessionCreator onSessionCreated={onSessionCreated} />
            </motion.div>
          )}
        </motion.div>

        {/* Footer */}
        <div className="absolute bottom-6 text-xs text-sketch-ink-faint font-body">
          Powered by Gemini + MCP
        </div>
      </div>
    </div>
  );
};

export default WelcomePage;
