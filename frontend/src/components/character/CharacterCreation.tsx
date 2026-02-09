/**
 * BG3-style Character Creation UI
 *
 * Multi-step wizard: Race -> Class -> Abilities -> Skills -> Name/Background -> Confirm
 */
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Loader2, ChevronLeft, ChevronRight, Check } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getCharacterCreationOptions, createCharacter } from '../../api';
import type {
  CharacterCreationOptions,
  CharacterCreationResponse,
} from '../../types';

interface CharacterCreationProps {
  worldId: string;
  sessionId: string;
  onComplete: (response: CharacterCreationResponse) => void;
}

const STEPS = [
  'selectRace',
  'selectClass',
  'allocateAbilities',
  'selectSkills',
  'nameAndStory',
  'confirm',
] as const;

const ABILITY_NAMES = ['STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA'] as const;
const ABILITY_KEYS = ['str', 'dex', 'con', 'int', 'wis', 'cha'] as const;

interface NamedEntry {
  name?: string;
  name_en?: string;
  name_zh?: string;
}

interface SkillChoiceObject {
  count?: number;
  from?: string[];
}

interface EquipmentItem {
  slot: string;
  item_id: string;
}

function abilityKeyToAbbr(key: string): string {
  return key.toUpperCase().slice(0, 3);
}

function getLocalizedName(entry: NamedEntry | undefined, isZh: boolean): string {
  if (!entry) return '';
  if (isZh) return entry.name ?? entry.name_zh ?? entry.name_en ?? '';
  return entry.name_en ?? entry.name_zh ?? entry.name ?? '';
}

function getHitDieValue(raw: number | string | undefined): number {
  if (typeof raw === 'number') return raw;
  if (typeof raw === 'string') {
    const match = raw.match(/(\d+)/);
    if (match) return Number(match[1]);
  }
  return 8;
}

function normalizeEquipment(
  raw: EquipmentItem[] | Record<string, string> | undefined,
): EquipmentItem[] {
  if (Array.isArray(raw)) return raw;
  if (!raw || typeof raw !== 'object') return [];
  return Object.entries(raw).map(([slot, itemId]) => ({ slot, item_id: itemId }));
}

function getModifier(score: number): string {
  const mod = Math.floor((score - 10) / 2);
  return mod >= 0 ? `+${mod}` : `${mod}`;
}

// Cost for point buy: 8-13 cost 1 each, 14 costs 2, 15 costs 2
function getDefaultCost(score: number): number {
  if (score <= 13) return score - 8;
  if (score === 14) return 7;
  if (score === 15) return 9;
  return 9;
}

export const CharacterCreation: React.FC<CharacterCreationProps> = ({
  worldId,
  sessionId,
  onComplete,
}) => {
  const { t, i18n } = useTranslation();
  const isZh = i18n.language.startsWith('zh');

  // Data loading
  const [options, setOptions] = useState<CharacterCreationOptions | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Wizard state
  const [currentStep, setCurrentStep] = useState(0);
  const [selectedRace, setSelectedRace] = useState<string>('');
  const [selectedClass, setSelectedClass] = useState<string>('');
  const [abilityScores, setAbilityScores] = useState<Record<string, number>>(() => {
    const scores: Record<string, number> = {};
    ABILITY_KEYS.forEach(k => { scores[k] = 8; });
    return scores;
  });
  const [selectedSkills, setSelectedSkills] = useState<string[]>([]);
  const [characterName, setCharacterName] = useState('');
  const [selectedBackground, setSelectedBackground] = useState('');
  const [backstory, setBackstory] = useState('');

  // Load options on mount
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const data = await getCharacterCreationOptions(worldId);
        if (!cancelled) {
          setOptions(data);
          // Default selections
          const raceKeys = Object.keys(data.races);
          if (raceKeys.length > 0) setSelectedRace(raceKeys[0]);
          const bgKeys = Object.keys(data.backgrounds);
          if (bgKeys.length > 0) setSelectedBackground(bgKeys[0]);
        }
      } catch (err) {
        if (!cancelled) {
          setLoadError('Failed to load character creation options.');
          console.error(err);
        }
      }
    };
    load();
    return () => { cancelled = true; };
  }, [worldId]);

  // Compute point buy cost
  const pointBuyConfig = options?.point_buy;
  const totalPoints = pointBuyConfig?.total_points ?? 27;
  const minScore = pointBuyConfig?.min ?? pointBuyConfig?.min_score ?? 8;
  const maxScore = pointBuyConfig?.max ?? pointBuyConfig?.max_score ?? 15;

  const pointsSpent = useMemo(() => {
    let spent = 0;
    for (const key of ABILITY_KEYS) {
      const score = abilityScores[key];
      if (pointBuyConfig?.cost_table) {
        spent += pointBuyConfig.cost_table[String(score)] ?? getDefaultCost(score);
      } else {
        spent += getDefaultCost(score);
      }
    }
    return spent;
  }, [abilityScores, pointBuyConfig]);

  const pointsRemaining = totalPoints - pointsSpent;

  // Racial bonuses for display
  const racialBonuses = useMemo(() => {
    if (!options || !selectedRace) return {};
    return options.races[selectedRace]?.ability_bonuses ?? {};
  }, [options, selectedRace]);

  // Class skill info
  const classData = options?.classes[selectedClass];
  const rawSkillChoices = classData?.skill_choices;
  const skillChoiceObject = (
    !Array.isArray(rawSkillChoices) && rawSkillChoices
      ? rawSkillChoices as SkillChoiceObject
      : undefined
  );
  const skillChoices = Array.isArray(rawSkillChoices)
    ? rawSkillChoices
    : (skillChoiceObject?.from ?? []);
  const skillCount = classData?.skill_count
    ?? skillChoiceObject?.count
    ?? skillChoices.length;

  // Reset skills when class changes
  useEffect(() => {
    setSelectedSkills([]);
  }, [selectedClass]);

  const handleAbilityChange = useCallback((key: string, delta: number) => {
    setAbilityScores(prev => {
      const current = prev[key];
      const next = current + delta;
      if (next < minScore || next > maxScore) return prev;

      // Check cost
      const currentCost = pointBuyConfig?.cost_table
        ? (pointBuyConfig.cost_table[String(current)] ?? getDefaultCost(current))
        : getDefaultCost(current);
      const nextCost = pointBuyConfig?.cost_table
        ? (pointBuyConfig.cost_table[String(next)] ?? getDefaultCost(next))
        : getDefaultCost(next);
      const costDelta = nextCost - currentCost;

      if (costDelta > 0 && costDelta > (totalPoints - pointsSpent + currentCost - currentCost)) {
        // Check if we can afford it
        const newSpent = pointsSpent + costDelta;
        if (newSpent > totalPoints) return prev;
      }

      return { ...prev, [key]: next };
    });
  }, [minScore, maxScore, pointBuyConfig, pointsSpent, totalPoints]);

  const toggleSkill = useCallback((skillId: string) => {
    setSelectedSkills(prev => {
      if (prev.includes(skillId)) {
        return prev.filter(s => s !== skillId);
      }
      if (prev.length >= skillCount) return prev;
      return [...prev, skillId];
    });
  }, [skillCount]);

  // Step validation
  const canAdvance = useMemo(() => {
    switch (STEPS[currentStep]) {
      case 'selectRace': return !!selectedRace;
      case 'selectClass': return !!selectedClass;
      case 'allocateAbilities': return pointsRemaining === 0;
      case 'selectSkills': return selectedSkills.length === skillCount;
      case 'nameAndStory': return characterName.trim().length > 0 && !!selectedBackground;
      case 'confirm': return true;
      default: return false;
    }
  }, [currentStep, selectedRace, selectedClass, pointsRemaining, selectedSkills, skillCount, characterName, selectedBackground]);

  const handleSubmit = async () => {
    if (!options) return;
    setIsSubmitting(true);
    try {
      const response = await createCharacter(worldId, sessionId, {
        name: characterName.trim(),
        race: selectedRace,
        character_class: selectedClass,
        background: selectedBackground,
        ability_scores: abilityScores,
        skill_proficiencies: selectedSkills,
        backstory: backstory.trim() || undefined,
      });
      onComplete(response);
    } catch (err) {
      console.error('Character creation failed:', err);
      setIsSubmitting(false);
    }
  };

  // Loading state
  if (!options && !loadError) {
    return (
      <div className="flex items-center justify-center min-h-[400px] text-[var(--g-title-text-muted)]">
        <Loader2 className="w-6 h-6 animate-spin mr-3" />
        <span className="font-body">{t('common.loading')}</span>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="flex items-center justify-center min-h-[400px] text-g-red">
        <span className="font-body">{loadError}</span>
      </div>
    );
  }

  const stepKey = STEPS[currentStep];

  return (
    <div className="w-full max-w-4xl mx-auto px-4 py-6">
      {/* Title */}
      <h1 className="font-heading text-2xl text-[var(--g-accent-gold)] text-center mb-2">
        {t('characterCreation.title')}
      </h1>

      {/* Step progress */}
      <div className="flex items-center justify-center gap-1 mb-6">
        {STEPS.map((step, i) => (
          <React.Fragment key={step}>
            <div
              className={`
                w-8 h-8 rounded-full flex items-center justify-center text-xs font-heading
                border transition-all duration-300
                ${i < currentStep
                  ? 'bg-[var(--g-accent-gold)] border-[var(--g-accent-gold)] text-white'
                  : i === currentStep
                    ? 'border-[var(--g-accent-gold)] text-[var(--g-accent-gold)] bg-[rgba(196,154,42,0.15)]'
                    : 'border-[var(--g-title-border)] text-[var(--g-title-text-muted)] bg-transparent'
                }
              `}
            >
              {i < currentStep ? <Check className="w-3.5 h-3.5" /> : i + 1}
            </div>
            {i < STEPS.length - 1 && (
              <div
                className={`w-6 h-px transition-colors duration-300 ${
                  i < currentStep ? 'bg-[var(--g-accent-gold)]' : 'bg-[var(--g-title-border)]'
                }`}
              />
            )}
          </React.Fragment>
        ))}
      </div>
      <p className="text-center text-xs font-body text-[var(--g-title-text-muted)] mb-6">
        {t('characterCreation.step', { current: currentStep + 1, total: STEPS.length })}
      </p>

      {/* Step content */}
      <AnimatePresence mode="wait">
        <motion.div
          key={stepKey}
          initial={{ opacity: 0, x: 30 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -30 }}
          transition={{ duration: 0.25 }}
          className="min-h-[360px]"
        >
          {stepKey === 'selectRace' && (
            <RaceStep
              options={options!}
              selectedRace={selectedRace}
              onSelect={setSelectedRace}
              isZh={isZh}
              t={t}
            />
          )}
          {stepKey === 'selectClass' && (
            <ClassStep
              options={options!}
              selectedClass={selectedClass}
              onSelect={setSelectedClass}
              isZh={isZh}
              t={t}
            />
          )}
          {stepKey === 'allocateAbilities' && (
            <AbilityStep
              abilityScores={abilityScores}
              racialBonuses={racialBonuses}
              pointsRemaining={pointsRemaining}
              minScore={minScore}
              maxScore={maxScore}
              onChange={handleAbilityChange}
              t={t}
            />
          )}
          {stepKey === 'selectSkills' && (
            <SkillStep
              options={options!}
              skillChoices={skillChoices}
              selectedSkills={selectedSkills}
              skillCount={skillCount}
              onToggle={toggleSkill}
              isZh={isZh}
              t={t}
            />
          )}
          {stepKey === 'nameAndStory' && (
            <NameStep
              options={options!}
              characterName={characterName}
              selectedBackground={selectedBackground}
              backstory={backstory}
              onNameChange={setCharacterName}
              onBackgroundChange={setSelectedBackground}
              onBackstoryChange={setBackstory}
              isZh={isZh}
              t={t}
            />
          )}
          {stepKey === 'confirm' && (
            <ConfirmStep
              options={options!}
              selectedRace={selectedRace}
              selectedClass={selectedClass}
              selectedBackground={selectedBackground}
              abilityScores={abilityScores}
              racialBonuses={racialBonuses}
              selectedSkills={selectedSkills}
              characterName={characterName}
              isZh={isZh}
              t={t}
            />
          )}
        </motion.div>
      </AnimatePresence>

      {/* Navigation buttons */}
      <div className="flex items-center justify-between mt-6 pt-4 border-t border-[var(--g-title-border)]">
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={() => setCurrentStep(s => Math.max(0, s - 1))}
          disabled={currentStep === 0}
          className="
            flex items-center gap-2 px-4 py-2
            font-heading text-sm
            text-[var(--g-title-text-muted)]
            border border-[var(--g-title-border)]
            rounded-lg
            hover:border-[var(--g-title-border-strong)] hover:text-[var(--g-title-text-primary)]
            disabled:opacity-30 disabled:cursor-not-allowed
            transition-all duration-200
          "
        >
          <ChevronLeft className="w-4 h-4" />
          {t('characterCreation.back')}
        </motion.button>

        {currentStep < STEPS.length - 1 ? (
          <motion.button
            whileHover={canAdvance ? { scale: 1.02 } : {}}
            whileTap={canAdvance ? { scale: 0.98 } : {}}
            onClick={() => setCurrentStep(s => Math.min(STEPS.length - 1, s + 1))}
            disabled={!canAdvance}
            className="
              flex items-center gap-2 px-5 py-2
              font-heading text-sm
              bg-[var(--g-accent-gold)] hover:bg-[var(--g-accent-gold-dark)]
              text-white
              rounded-lg
              border border-[var(--g-accent-gold)]
              shadow-g-gold
              disabled:opacity-40 disabled:cursor-not-allowed
              transition-all duration-200
            "
          >
            {t('characterCreation.next')}
            <ChevronRight className="w-4 h-4" />
          </motion.button>
        ) : (
          <motion.button
            whileHover={!isSubmitting ? { scale: 1.02, y: -1 } : {}}
            whileTap={!isSubmitting ? { scale: 0.98 } : {}}
            onClick={handleSubmit}
            disabled={isSubmitting}
            className="
              flex items-center gap-2 px-6 py-2.5
              font-heading text-sm
              bg-[var(--g-accent-gold)] hover:bg-[var(--g-accent-gold-dark)]
              text-white
              rounded-lg
              border border-[var(--g-accent-gold)]
              shadow-g-gold
              disabled:opacity-50 disabled:cursor-not-allowed
              transition-all duration-200
            "
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                {t('characterCreation.creating')}
              </>
            ) : (
              <>
                <Check className="w-4 h-4" />
                {t('characterCreation.confirm')}
              </>
            )}
          </motion.button>
        )}
      </div>
    </div>
  );
};

// =============================================================================
// Step Components
// =============================================================================

interface StepProps {
  t: (key: string, opts?: Record<string, unknown>) => string;
  isZh: boolean;
}

// --- Race Step ---
const RaceStep: React.FC<StepProps & {
  options: CharacterCreationOptions;
  selectedRace: string;
  onSelect: (race: string) => void;
}> = ({ options, selectedRace, onSelect, isZh, t }) => {
  const raceEntries = Object.entries(options.races);
  const currentRace = options.races[selectedRace];

  return (
    <div>
      <h2 className="font-heading text-lg text-[var(--g-title-text-primary)] mb-4">
        {t('characterCreation.selectRace')}
      </h2>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mb-4">
        {raceEntries.map(([key, race]) => (
          <motion.button
            key={key}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.97 }}
            onClick={() => onSelect(key)}
            className={`
              text-left p-3 rounded-lg border transition-all duration-200
              ${selectedRace === key
                ? 'border-[var(--g-accent-gold)] bg-[rgba(196,154,42,0.12)] shadow-g-card-glow'
                : 'border-[var(--g-title-border)] bg-[rgba(26,21,16,0.4)] hover:border-[var(--g-title-border-strong)]'
              }
            `}
          >
            <div className="font-heading text-sm text-[var(--g-title-text-primary)]">
              {getLocalizedName(race, isZh)}
            </div>
            <div className="flex flex-wrap gap-1 mt-1.5">
              {Object.entries(race.ability_bonuses).map(([ability, bonus]) => (
                <span
                  key={ability}
                  className="text-[10px] font-body px-1.5 py-0.5 rounded bg-[rgba(196,154,42,0.15)] text-[var(--g-accent-gold)]"
                >
                  {abilityKeyToAbbr(ability)} +{bonus}
                </span>
              ))}
            </div>
          </motion.button>
        ))}
      </div>

      {/* Detail panel */}
      {currentRace && (
        <motion.div
          key={selectedRace}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="p-4 rounded-lg border border-[var(--g-title-border)] bg-[rgba(26,21,16,0.6)]"
        >
          <p className="font-body text-sm text-[var(--g-title-text-muted)] mb-3">
            {currentRace.description}
          </p>
          <div className="flex items-center gap-4 text-xs font-body text-[var(--g-title-text-muted)]">
            <span>{t('characterCreation.speed')}: {currentRace.speed}ft</span>
          </div>
          <div className="mt-2">
            <span className="text-xs font-body text-[var(--g-title-text-muted)]">
              {t('characterCreation.traits')}:
            </span>
            <div className="flex flex-wrap gap-1 mt-1">
              {currentRace.racial_traits.map((trait, i) => (
                <span
                  key={i}
                  className="text-[10px] font-body px-2 py-0.5 rounded-full border border-[var(--g-title-border)] text-[var(--g-title-text-muted)]"
                >
                  {trait}
                </span>
              ))}
            </div>
          </div>
        </motion.div>
      )}
    </div>
  );
};

// --- Class Step ---
const ClassStep: React.FC<StepProps & {
  options: CharacterCreationOptions;
  selectedClass: string;
  onSelect: (cls: string) => void;
}> = ({ options, selectedClass, onSelect, isZh, t }) => {
  const classEntries = Object.entries(options.classes);
  const currentClass = options.classes[selectedClass];
  const currentHitDieValue = getHitDieValue(currentClass?.hit_die ?? currentClass?.hit_dice);
  const currentSkillChoices = currentClass?.skill_choices;
  const currentSkillCount = currentClass?.skill_count ?? (
    Array.isArray(currentSkillChoices)
      ? currentSkillChoices.length
      : ((currentSkillChoices as SkillChoiceObject | undefined)?.count ?? 0)
  );
  const currentClassFeatures = currentClass?.class_features ?? currentClass?.features ?? [];

  return (
    <div>
      <h2 className="font-heading text-lg text-[var(--g-title-text-primary)] mb-4">
        {t('characterCreation.selectClass')}
      </h2>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        {classEntries.map(([key, cls]) => (
          <motion.button
            key={key}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.97 }}
            onClick={() => onSelect(key)}
            className={`
              text-left p-3 rounded-lg border transition-all duration-200
              ${selectedClass === key
                ? 'border-[var(--g-accent-gold)] bg-[rgba(196,154,42,0.12)] shadow-g-card-glow'
                : 'border-[var(--g-title-border)] bg-[rgba(26,21,16,0.4)] hover:border-[var(--g-title-border-strong)]'
              }
            `}
          >
            <div className="font-heading text-sm text-[var(--g-title-text-primary)]">
              {getLocalizedName(cls, isZh)}
            </div>
            <div className="text-[10px] font-body text-[var(--g-title-text-muted)] mt-1">
              {t('characterCreation.hitDice')}: d{getHitDieValue(cls.hit_die ?? cls.hit_dice)}
            </div>
          </motion.button>
        ))}
      </div>

      {/* Detail panel */}
      {currentClass && (
        <motion.div
          key={selectedClass}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="p-4 rounded-lg border border-[var(--g-title-border)] bg-[rgba(26,21,16,0.6)]"
        >
          <p className="font-body text-sm text-[var(--g-title-text-muted)] mb-3">
            {currentClass.description}
          </p>
          <div className="grid grid-cols-2 gap-3 text-xs font-body">
            <div>
              <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.primaryAbility')}: </span>
              <span className="text-[var(--g-accent-gold)]">{abilityKeyToAbbr(currentClass.primary_ability)}</span>
            </div>
            <div>
              <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.startingHP')}: </span>
              <span className="text-[var(--g-title-text-primary)]">{currentHitDieValue}</span>
            </div>
            <div>
              <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.savingThrows')}: </span>
              <span className="text-[var(--g-title-text-primary)]">
                {currentClass.saving_throws.map(s => abilityKeyToAbbr(s)).join(', ')}
              </span>
            </div>
            <div>
              <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.skills')}: </span>
              <span className="text-[var(--g-title-text-primary)]">
                {t('characterCreation.selectCount', { count: currentSkillCount })}
              </span>
            </div>
          </div>
          {(currentClassFeatures.length ?? 0) > 0 && (
            <div className="mt-3">
              <span className="text-xs font-body text-[var(--g-title-text-muted)]">
                {t('characterCreation.features')}:
              </span>
              <div className="flex flex-wrap gap-1 mt-1">
                {currentClassFeatures.map((feat: string, i: number) => (
                  <span
                    key={i}
                    className="text-[10px] font-body px-2 py-0.5 rounded-full border border-[var(--g-title-border)] text-[var(--g-title-text-muted)]"
                  >
                    {feat}
                  </span>
                ))}
              </div>
            </div>
          )}
        </motion.div>
      )}
    </div>
  );
};

// --- Ability Step ---
const AbilityStep: React.FC<{
  abilityScores: Record<string, number>;
  racialBonuses: Record<string, number>;
  pointsRemaining: number;
  minScore: number;
  maxScore: number;
  onChange: (key: string, delta: number) => void;
  t: (key: string, opts?: Record<string, unknown>) => string;
}> = ({ abilityScores, racialBonuses, pointsRemaining, minScore, maxScore, onChange, t }) => {
  return (
    <div>
      <h2 className="font-heading text-lg text-[var(--g-title-text-primary)] mb-2">
        {t('characterCreation.allocateAbilities')}
      </h2>
      <p className={`
        font-body text-sm text-center mb-5 px-3 py-2 rounded-lg border
        ${pointsRemaining < 0
          ? 'border-red-500/50 text-red-400 bg-red-500/10'
          : pointsRemaining === 0
            ? 'border-[var(--g-accent-gold)]/50 text-[var(--g-accent-gold)] bg-[rgba(196,154,42,0.1)]'
            : 'border-[var(--g-title-border)] text-[var(--g-title-text-muted)] bg-[rgba(26,21,16,0.4)]'
        }
      `}>
        {t('characterCreation.pointsRemaining', { points: pointsRemaining })}
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {ABILITY_KEYS.map((key, i) => {
          const baseScore = abilityScores[key];
          const bonus = racialBonuses[key] ?? 0;
          const finalScore = baseScore + bonus;
          return (
            <div
              key={key}
              className="p-3 rounded-lg border border-[var(--g-title-border)] bg-[rgba(26,21,16,0.4)] text-center"
            >
              <div className="font-heading text-xs text-[var(--g-title-text-muted)] mb-1">
                {ABILITY_NAMES[i]}
              </div>
              <div className="flex items-center justify-center gap-2 mb-1">
                <button
                  onClick={() => onChange(key, -1)}
                  disabled={baseScore <= minScore}
                  className="
                    w-7 h-7 rounded-md
                    border border-[var(--g-title-border)]
                    text-[var(--g-title-text-muted)]
                    hover:border-[var(--g-accent-gold)] hover:text-[var(--g-accent-gold)]
                    disabled:opacity-20 disabled:cursor-not-allowed
                    transition-all duration-150
                    flex items-center justify-center text-lg leading-none
                  "
                >
                  -
                </button>
                <span className="font-heading text-xl text-[var(--g-title-text-primary)] w-8 text-center">
                  {baseScore}
                </span>
                <button
                  onClick={() => onChange(key, 1)}
                  disabled={baseScore >= maxScore || pointsRemaining <= 0}
                  className="
                    w-7 h-7 rounded-md
                    border border-[var(--g-title-border)]
                    text-[var(--g-title-text-muted)]
                    hover:border-[var(--g-accent-gold)] hover:text-[var(--g-accent-gold)]
                    disabled:opacity-20 disabled:cursor-not-allowed
                    transition-all duration-150
                    flex items-center justify-center text-lg leading-none
                  "
                >
                  +
                </button>
              </div>
              {bonus > 0 && (
                <div className="text-[10px] font-body text-[var(--g-accent-gold)]">
                  {t('characterCreation.raceBonus')} +{bonus}
                </div>
              )}
              <div className="text-xs font-body text-[var(--g-title-text-muted)] mt-0.5">
                {t('characterCreation.confirm') === 'Confirm & Create' ? 'Total' : '总计'}: {finalScore} ({getModifier(finalScore)})
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

// --- Skill Step ---
const SkillStep: React.FC<StepProps & {
  options: CharacterCreationOptions;
  skillChoices: string[];
  selectedSkills: string[];
  skillCount: number;
  onToggle: (skillId: string) => void;
}> = ({ options, skillChoices, selectedSkills, skillCount, onToggle, isZh, t }) => {
  return (
    <div>
      <h2 className="font-heading text-lg text-[var(--g-title-text-primary)] mb-2">
        {t('characterCreation.selectSkills')}
      </h2>
      <p className="font-body text-sm text-[var(--g-title-text-muted)] mb-4">
        {t('characterCreation.selectCount', { count: skillCount })}
        <span className="ml-2 text-[var(--g-accent-gold)]">
          ({selectedSkills.length}/{skillCount})
        </span>
      </p>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
        {skillChoices.map((skillId) => {
          const skillInfo = options.skills[skillId];
          const isSelected = selectedSkills.includes(skillId);
          const isDisabled = !isSelected && selectedSkills.length >= skillCount;
          return (
            <motion.button
              key={skillId}
              whileTap={!isDisabled ? { scale: 0.97 } : {}}
              onClick={() => !isDisabled && onToggle(skillId)}
              disabled={isDisabled}
              className={`
                text-left p-2.5 rounded-lg border transition-all duration-200
                ${isSelected
                  ? 'border-[var(--g-accent-gold)] bg-[rgba(196,154,42,0.12)]'
                  : isDisabled
                    ? 'border-[var(--g-title-border)] bg-[rgba(26,21,16,0.2)] opacity-40 cursor-not-allowed'
                    : 'border-[var(--g-title-border)] bg-[rgba(26,21,16,0.4)] hover:border-[var(--g-title-border-strong)]'
                }
              `}
            >
              <div className="flex items-center gap-2">
                <div className={`
                  w-4 h-4 rounded border flex items-center justify-center flex-shrink-0
                  ${isSelected
                    ? 'border-[var(--g-accent-gold)] bg-[var(--g-accent-gold)]'
                    : 'border-[var(--g-title-border)]'
                  }
                `}>
                  {isSelected && <Check className="w-3 h-3 text-white" />}
                </div>
                <div>
                  <div className="font-body text-xs text-[var(--g-title-text-primary)]">
                    {getLocalizedName(skillInfo, isZh) || skillId.replace(/_/g, ' ')}
                  </div>
                  {skillInfo && (
                    <div className="text-[10px] text-[var(--g-title-text-muted)]">
                      ({abilityKeyToAbbr(skillInfo.ability)})
                    </div>
                  )}
                </div>
              </div>
            </motion.button>
          );
        })}
      </div>
    </div>
  );
};

// --- Name & Background Step ---
const NameStep: React.FC<StepProps & {
  options: CharacterCreationOptions;
  characterName: string;
  selectedBackground: string;
  backstory: string;
  onNameChange: (name: string) => void;
  onBackgroundChange: (bg: string) => void;
  onBackstoryChange: (story: string) => void;
}> = ({ options, characterName, selectedBackground, backstory, onNameChange, onBackgroundChange, onBackstoryChange, isZh, t }) => {
  const bgEntries = Object.entries(options.backgrounds);
  const currentBg = options.backgrounds[selectedBackground];

  return (
    <div>
      <h2 className="font-heading text-lg text-[var(--g-title-text-primary)] mb-4">
        {t('characterCreation.nameAndStory')}
      </h2>

      {/* Name input */}
      <label className="block mb-4">
        <span className="text-xs font-body text-[var(--g-title-text-muted)] mb-1 block">
          {t('characterCreation.name')}
        </span>
        <input
          type="text"
          value={characterName}
          onChange={(e) => onNameChange(e.target.value)}
          maxLength={50}
          className="
            w-full px-3 py-2.5
            font-body text-sm
            bg-[rgba(26,21,16,0.6)]
            text-[var(--g-title-text-primary)]
            border border-[var(--g-title-border)]
            rounded-lg
            focus:border-[var(--g-accent-gold)]
            focus:outline-none
            transition-colors
            placeholder:text-[var(--g-title-text-muted)]
          "
          placeholder={isZh ? '输入角色名称...' : 'Enter character name...'}
        />
      </label>

      {/* Background selection */}
      <label className="block mb-4">
        <span className="text-xs font-body text-[var(--g-title-text-muted)] mb-1 block">
          {t('characterCreation.background')}
        </span>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {bgEntries.map(([key, bg]) => (
            <motion.button
              key={key}
              whileTap={{ scale: 0.97 }}
              onClick={() => onBackgroundChange(key)}
              className={`
                text-left p-2.5 rounded-lg border transition-all duration-200
                ${selectedBackground === key
                  ? 'border-[var(--g-accent-gold)] bg-[rgba(196,154,42,0.12)]'
                  : 'border-[var(--g-title-border)] bg-[rgba(26,21,16,0.4)] hover:border-[var(--g-title-border-strong)]'
                }
              `}
            >
              <div className="font-body text-xs text-[var(--g-title-text-primary)]">
                {getLocalizedName(bg, isZh)}
              </div>
            </motion.button>
          ))}
        </div>
      </label>

      {/* Background detail */}
      {currentBg && (
        <div className="p-3 rounded-lg border border-[var(--g-title-border)] bg-[rgba(26,21,16,0.4)] mb-4">
          <p className="font-body text-xs text-[var(--g-title-text-muted)] mb-2">{currentBg.description}</p>
          <div className="flex flex-wrap gap-1">
            {currentBg.skill_proficiencies.map((skill, i) => (
              <span
                key={i}
                className="text-[10px] font-body px-1.5 py-0.5 rounded bg-[rgba(196,154,42,0.15)] text-[var(--g-accent-gold)]"
              >
                {skill}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Backstory textarea */}
      <label className="block">
        <span className="text-xs font-body text-[var(--g-title-text-muted)] mb-1 block">
          {t('characterCreation.backstory')}
        </span>
        <textarea
          value={backstory}
          onChange={(e) => onBackstoryChange(e.target.value)}
          rows={3}
          maxLength={500}
          className="
            w-full px-3 py-2.5
            font-body text-sm
            bg-[rgba(26,21,16,0.6)]
            text-[var(--g-title-text-primary)]
            border border-[var(--g-title-border)]
            rounded-lg
            focus:border-[var(--g-accent-gold)]
            focus:outline-none
            transition-colors
            placeholder:text-[var(--g-title-text-muted)]
            resize-none
          "
          placeholder={t('characterCreation.backstoryHint')}
        />
      </label>
    </div>
  );
};

// --- Confirm Step ---
const ConfirmStep: React.FC<StepProps & {
  options: CharacterCreationOptions;
  selectedRace: string;
  selectedClass: string;
  selectedBackground: string;
  abilityScores: Record<string, number>;
  racialBonuses: Record<string, number>;
  selectedSkills: string[];
  characterName: string;
}> = ({
  options, selectedRace, selectedClass, selectedBackground,
  abilityScores, racialBonuses, selectedSkills, characterName,
  isZh, t,
}) => {
  const race = options.races[selectedRace];
  const cls = options.classes[selectedClass];
  const bg = options.backgrounds[selectedBackground];
  const hitDieValue = getHitDieValue(cls?.hit_die ?? cls?.hit_dice);
  const equipmentItems = normalizeEquipment(cls?.starting_equipment);

  return (
    <div>
      <h2 className="font-heading text-lg text-[var(--g-title-text-primary)] mb-4">
        {t('characterCreation.summary')}
      </h2>

      <div className="p-5 rounded-lg border border-[var(--g-accent-gold)]/40 bg-[rgba(196,154,42,0.05)]">
        {/* Character name */}
        <div className="text-center mb-4">
          <h3 className="font-heading text-xl text-[var(--g-accent-gold)]">
            {characterName}
          </h3>
          <p className="font-body text-xs text-[var(--g-title-text-muted)] mt-1">
            {getLocalizedName(race, isZh)} {getLocalizedName(cls, isZh)}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-xs font-body">
          <div className="flex justify-between">
            <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.race')}:</span>
            <span className="text-[var(--g-title-text-primary)]">{getLocalizedName(race, isZh)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.class')}:</span>
            <span className="text-[var(--g-title-text-primary)]">{getLocalizedName(cls, isZh)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.background')}:</span>
            <span className="text-[var(--g-title-text-primary)]">{getLocalizedName(bg, isZh)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.level')}:</span>
            <span className="text-[var(--g-title-text-primary)]">1</span>
          </div>
          <div className="flex justify-between">
            <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.hp')}:</span>
            <span className="text-[var(--g-title-text-primary)]">
              {hitDieValue + Math.floor(((abilityScores.con + (racialBonuses.con ?? 0)) - 10) / 2)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-[var(--g-title-text-muted)]">{t('characterCreation.speed')}:</span>
            <span className="text-[var(--g-title-text-primary)]">{race?.speed ?? 30}ft</span>
          </div>
        </div>

        {/* Ability scores */}
        <div className="mt-4 pt-3 border-t border-[var(--g-title-border)]">
          <div className="text-xs font-body text-[var(--g-title-text-muted)] mb-2">
            {t('characterCreation.abilities')}
          </div>
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
            {ABILITY_KEYS.map((key, i) => {
              const base = abilityScores[key];
              const bonus = racialBonuses[key] ?? 0;
              const total = base + bonus;
              return (
                <div key={key} className="text-center p-2 rounded border border-[var(--g-title-border)] bg-[rgba(26,21,16,0.4)]">
                  <div className="text-[10px] font-heading text-[var(--g-title-text-muted)]">{ABILITY_NAMES[i]}</div>
                  <div className="font-heading text-lg text-[var(--g-title-text-primary)]">{total}</div>
                  <div className="text-[10px] font-body text-[var(--g-title-text-muted)]">{getModifier(total)}</div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Skills */}
        <div className="mt-4 pt-3 border-t border-[var(--g-title-border)]">
          <div className="text-xs font-body text-[var(--g-title-text-muted)] mb-2">
            {t('characterCreation.proficiencies')}
          </div>
          <div className="flex flex-wrap gap-1">
            {selectedSkills.map((skillId) => {
              const skillInfo = options.skills[skillId];
              return (
                <span
                  key={skillId}
                  className="text-[10px] font-body px-2 py-0.5 rounded-full border border-[var(--g-accent-gold)]/40 text-[var(--g-accent-gold)] bg-[rgba(196,154,42,0.08)]"
                >
                  {getLocalizedName(skillInfo, isZh) || skillId.replace(/_/g, ' ')}
                </span>
              );
            })}
          </div>
        </div>

        {/* Equipment */}
        {cls && equipmentItems.length > 0 && (
          <div className="mt-4 pt-3 border-t border-[var(--g-title-border)]">
            <div className="text-xs font-body text-[var(--g-title-text-muted)] mb-2">
              {t('characterCreation.equipment')}
            </div>
            <div className="flex flex-wrap gap-1">
              {equipmentItems.map((equip) => (
                <span
                  key={equip.slot}
                  className="text-[10px] font-body px-2 py-0.5 rounded-full border border-[var(--g-title-border)] text-[var(--g-title-text-muted)]"
                >
                  {equip.slot}: {equip.item_id.replace(/_/g, ' ')}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default CharacterCreation;
