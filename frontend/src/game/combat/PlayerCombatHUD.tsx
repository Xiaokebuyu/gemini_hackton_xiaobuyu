import { useCombatStore } from '../../stores/combatStore'

export default function PlayerCombatHUD() {
  const { playerHp, playerMaxHp, playerAc, playerEffects } = useCombatStore()
  const pct = playerMaxHp > 0 ? Math.max(0, playerHp / playerMaxHp) : 0
  const barColor =
    pct > 0.5 ? 'bg-green-600' : pct > 0.25 ? 'bg-yellow-500' : 'bg-red-600'

  return (
    <div className="absolute left-4 bottom-[38vh] mb-2 bg-gray-900/90 border border-gray-700 rounded-lg p-3 w-44">
      {/* HP */}
      <div className="flex items-center gap-1 mb-1">
        <span className="text-red-400 text-sm">❤️</span>
        <span className="text-white text-xs font-semibold">{playerHp}/{playerMaxHp}</span>
      </div>
      <div className="w-full bg-gray-700 rounded-full h-1.5 mb-2">
        <div
          className={`${barColor} h-1.5 rounded-full transition-all duration-300`}
          style={{ width: `${pct * 100}%` }}
        />
      </div>

      {/* AC */}
      <div className="flex items-center gap-1 mb-1">
        <span className="text-blue-400 text-sm">🛡</span>
        <span className="text-white text-xs">AC {playerAc}</span>
      </div>

      {/* Effects */}
      {playerEffects.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1">
          {playerEffects.slice(0, 4).map((eff) => (
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
