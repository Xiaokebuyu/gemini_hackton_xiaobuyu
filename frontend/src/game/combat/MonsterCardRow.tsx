import { useCombatStore } from '../../stores/combatStore'
import MonsterCard from './MonsterCard'

export default function MonsterCardRow() {
  const participants = useCombatStore((s) => s.participants)
  const monsters = participants.filter((p) => !p.is_player)

  if (monsters.length === 0) return null

  return (
    <div className="absolute top-16 left-0 right-0 flex justify-center gap-3 px-4">
      {monsters.slice(0, 4).map((m) => (
        <MonsterCard key={m.id} participant={m} />
      ))}
    </div>
  )
}
