import { useEffect } from 'react'
import { useCombatStore } from '../../stores/combatStore'

export default function DiceRollOverlay() {
  const diceRollQueue = useCombatStore((s) => s.diceRollQueue)
  const dismissDiceRoll = useCombatStore((s) => s.dismissDiceRoll)
  const entry = diceRollQueue[0]

  useEffect(() => {
    if (!entry) return
    const timer = setTimeout(() => {
      dismissDiceRoll()
    }, 3000)
    return () => clearTimeout(timer)
  }, [entry?.id, dismissDiceRoll]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!entry) return null

  const { type, result, modifier, total, dc, success, skill, roller_name } = entry
  const modStr = modifier >= 0 ? `+${modifier}` : `${modifier}`
  const skillLabel: Record<string, string> = {
    stealth: '潜行', attack: '攻击', perception: '感知',
  }

  return (
    <div className="absolute inset-0 flex items-center justify-center z-50 pointer-events-none">
      <div className="pointer-events-auto bg-gray-950/95 backdrop-blur border border-amber-600 rounded-xl p-5 w-64 shadow-2xl">
        {/* 关闭 */}
        <button
          onClick={dismissDiceRoll}
          className="absolute top-2 right-3 text-gray-500 hover:text-gray-300 text-lg leading-none"
        >
          ×
        </button>

        {/* 骰子展示 */}
        <div className="text-center mb-3">
          <div className="text-4xl mb-1">🎲</div>
          <div className="text-gray-400 text-xs uppercase tracking-wide">
            {type} · {skillLabel[skill] ?? skill} · {roller_name}
          </div>
        </div>

        {/* 数字 */}
        <div className="text-center space-y-1">
          <div className="text-white text-2xl font-bold">
            {result} <span className="text-gray-400 text-lg">{modStr}</span>
            {' '}= <span className={success ? 'text-green-400' : 'text-red-400'}>{total}</span>
          </div>
          {dc > 0 && (
            <div className="text-gray-400 text-sm">vs DC {dc}</div>
          )}
        </div>

        {/* 结果 */}
        <div className={`text-center mt-2 text-sm font-semibold ${success ? 'text-green-400' : 'text-red-400'}`}>
          {success ? '✓ 命中' : '✗ 未命中'}
        </div>
      </div>
    </div>
  )
}
