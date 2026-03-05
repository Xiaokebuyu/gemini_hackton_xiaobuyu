import { useCombatStore } from '../../stores/combatStore'
import MonsterCardRow from './MonsterCardRow'
import PlayerCombatHUD from './PlayerCombatHUD'
import CombatLog from './CombatLog'
import CombatActionBar from './CombatActionBar'
import DiceRollOverlay from './DiceRollOverlay'
import DamageNumberLayer from './DamageNumberLayer'
import CombatEndSummary from './CombatEndSummary'

interface Props {
  onCombatAction: (type: string) => void
  onContinue: () => void
}

export default function CombatHUD({ onCombatAction, onContinue }: Props) {
  const combatEndData = useCombatStore((s) => s.combatEndData)

  return (
    <>
      <MonsterCardRow />
      <PlayerCombatHUD />
      <CombatLog />
      <CombatActionBar onAction={onCombatAction} />
      <DiceRollOverlay />
      <DamageNumberLayer />
      {combatEndData && <CombatEndSummary onContinue={onContinue} />}
    </>
  )
}
