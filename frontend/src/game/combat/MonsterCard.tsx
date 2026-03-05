import type { CombatParticipant } from '../../types/combat'

interface Props {
  participant: CombatParticipant
}

export default function MonsterCard({ participant }: Props) {
  const { name, hp, max_hp, status_effects, is_dead } = participant
  const pct = max_hp > 0 ? Math.max(0, hp / max_hp) : 0
  const barColor =
    pct > 0.5 ? 'bg-red-600' : pct > 0.25 ? 'bg-orange-500' : 'bg-red-900'

  return (
    <div
      className={`
        bg-gray-900/90 border border-red-800 rounded-lg p-3 w-36 flex flex-col gap-1
        ${is_dead ? 'opacity-40 grayscale' : ''}
      `}
    >
      {/* Avatar */}
      <div className="w-10 h-10 rounded-full bg-red-900 border border-red-700 flex items-center justify-center mx-auto">
        <span className="text-white font-bold text-lg select-none">
          {name[0]?.toUpperCase() ?? '?'}
        </span>
      </div>

      {/* Name */}
      <div className="text-center text-white text-xs font-semibold truncate">
        {name}{is_dead && ' 💀'}
      </div>

      {/* HP bar */}
      <div className="w-full bg-gray-700 rounded-full h-1.5">
        <div
          className={`${barColor} h-1.5 rounded-full transition-all duration-300`}
          style={{ width: `${pct * 100}%` }}
        />
      </div>
      <div className="text-center text-gray-400 text-xs">
        {hp}/{max_hp}
      </div>

      {/* Status effects */}
      {status_effects.length > 0 && (
        <div className="flex flex-wrap gap-1 justify-center">
          {status_effects.slice(0, 3).map((eff) => (
            <span
              key={eff}
              className="text-xs bg-purple-900 text-purple-200 rounded px-1 py-0.5 leading-none"
            >
              {eff}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
