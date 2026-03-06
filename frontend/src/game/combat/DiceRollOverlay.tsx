import { useEffect } from 'react'
import { useCombatStore } from '../../stores/combatStore'

const skillLabel: Record<string, string> = {
  stealth: '潜行',
  attack: '攻击',
  monster_attack: '攻击',
  perception: '感知',
  persuasion: '说服',
  athletics: '运动',
  investigation: '调查',
  survival: '求生',
  insight: '洞察',
  deception: '欺瞒',
  intimidation: '威吓',
  history: '历史',
  arcana: '奥秘',
  nature: '自然',
  religion: '宗教',
  medicine: '医药',
  acrobatics: '杂技',
  sleight_of_hand: '巧手',
  animal_handling: '驯兽',
  saving_throw: '豁免',
  investigate: '调查',
  contest: '对抗',
}

function formatSkill(skill: string) {
  return skillLabel[skill] ?? skill.replace(/_/g, ' ')
}

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
  const outcomeLabel = success ? '检定成功' : '检定失败'

  return (
    <div className="absolute inset-0 flex items-center justify-center z-50 pointer-events-none">
      <div className="pointer-events-auto bg-gray-950/95 backdrop-blur border border-amber-600 rounded-xl p-5 w-64 shadow-2xl">
        <button
          onClick={dismissDiceRoll}
          className="absolute top-2 right-3 text-gray-500 hover:text-gray-300 text-lg leading-none"
        >
          ×
        </button>

        <div className="text-center mb-3">
          <div className="text-white text-xs uppercase tracking-[0.28em] mb-2">Dice Roll</div>
          <div className="text-gray-400 text-xs uppercase tracking-wide">
            {type} · {formatSkill(skill)} · {roller_name}
          </div>
        </div>

        <div className="text-center space-y-1">
          <div className="text-white text-2xl font-bold">
            {result} <span className="text-gray-400 text-lg">{modStr}</span>
            {' '}= <span className={success ? 'text-green-400' : 'text-red-400'}>{total}</span>
          </div>
          {dc > 0 && (
            <div className="text-gray-400 text-sm">vs DC {dc}</div>
          )}
        </div>

        <div className={`text-center mt-3 text-sm font-semibold ${success ? 'text-green-400' : 'text-red-400'}`}>
          {outcomeLabel}
        </div>
      </div>
    </div>
  )
}
