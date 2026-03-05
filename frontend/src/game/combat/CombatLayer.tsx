import { useCombatStore } from '../../stores/combatStore'
import { useSceneStore } from '../../stores/sceneStore'
import CombatHUD from './CombatHUD'

interface Props {
  onCombatAction: (type: string, params?: Record<string, unknown>) => void
  onEncounterAction: (choice: string, subAreaId: string) => void
}

export default function CombatLayer({ onCombatAction }: Props) {
  const resetCombat = useCombatStore((s) => s.resetCombat)
  const setGameMode = useSceneStore((s) => s.setGameMode)

  const handleContinue = () => {
    resetCombat()
    setGameMode('explore')
  }

  return (
    <div className="absolute inset-0 z-10">
      {/* 红色氛围滤镜 */}
      <div className="absolute inset-0 bg-red-950/20 pointer-events-none" />

      {/* 战斗 HUD */}
      <CombatHUD
        onCombatAction={(type) => onCombatAction(type, {})}
        onContinue={handleContinue}
      />
    </div>
  )
}
