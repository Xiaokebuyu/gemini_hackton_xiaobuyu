/**
 * Welcome page - entry point for new sessions
 */
import React, { useState } from 'react';
import { motion } from 'framer-motion';
import { Compass } from 'lucide-react';
import { LanguageSwitch } from '../shared';
import SessionCreator from './SessionCreator';

import type { CreateGameSessionResponse } from '../../types';

interface WelcomePageProps {
  onSessionCreated: (worldId: string, sessionId: string, createResponse?: CreateGameSessionResponse) => void;
}

export const WelcomePage: React.FC<WelcomePageProps> = ({ onSessionCreated }) => {
  const [showCreator, setShowCreator] = useState(false);

  return (
    <div className="h-screen w-screen overflow-y-auto bg-g-bg-base relative">
      {/* Language switch */}
      <div className="absolute top-4 right-4 z-50">
        <LanguageSwitch />
      </div>

      {/* Center content */}
      <div className="relative z-10 min-h-full flex flex-col items-center justify-center py-8">
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
              bg-g-gold
              rounded-full
              flex items-center justify-center
              shadow-g-gold
            "
          >
            <Compass className="w-10 h-10 text-white" />
          </motion.div>

          {/* Title */}
          <h1 className="font-heading text-4xl text-g-text-primary mb-2">
            Chronicle
          </h1>
          <p className="font-body text-g-text-muted mb-8 text-sm">
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
                bg-g-gold hover:bg-g-gold-dark
                text-white
                font-heading text-lg
                rounded-lg
                border border-g-gold
                shadow-g-md
                hover:shadow-g-gold
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
        <div className="absolute bottom-6 text-xs text-g-text-muted font-body">
          Powered by Gemini + MCP
        </div>
      </div>
    </div>
  );
};

export default WelcomePage;
