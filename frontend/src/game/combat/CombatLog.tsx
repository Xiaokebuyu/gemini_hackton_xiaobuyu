import { useCombatStore } from '../../stores/combatStore'

export default function CombatLog() {
  const combatLog = useCombatStore((s) => s.combatLog)
  const visible = combatLog.slice(-8)

  if (visible.length === 0) return null

  return (
    <div className="absolute left-2 top-1/2 -translate-y-1/2 w-52 bg-gray-950/70 rounded-lg p-2 space-y-0.5 pointer-events-none">
      {visible.map((entry, i) => (
        <p key={i} className="text-xs text-gray-300 leading-snug">
          {entry}
        </p>
      ))}
    </div>
  )
}
