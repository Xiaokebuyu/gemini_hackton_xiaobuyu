/**
 * Combat trigger card — shown in narrative when combat is available but not yet entered
 */
import React from 'react';
import { Swords } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useCombatStore } from '../../stores';

const CombatTriggerCard: React.FC = () => {
  const { t } = useTranslation();
  const { setActive } = useCombatStore();

  return (
    <div className="mx-4 my-3 rounded-lg border border-red-500/50 bg-red-950/20 p-4">
      <div className="flex items-center gap-3 mb-3">
        <div className="p-2 rounded-lg bg-red-500/20">
          <Swords className="w-5 h-5 text-red-400" />
        </div>
        <h3 className="text-red-300 font-semibold text-lg">
          {t('combat.battleStart')}
        </h3>
      </div>
      <button
        onClick={() => setActive(true)}
        className="w-full py-2 px-4 rounded-md bg-red-600 hover:bg-red-500 text-white font-medium transition-colors"
      >
        {t('combat.enterBattle')}
      </button>
    </div>
  );
};

export default CombatTriggerCard;
