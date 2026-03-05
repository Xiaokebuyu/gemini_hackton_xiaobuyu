import { useCombatStore } from '../../stores/combatStore'
import type { DamageNumber } from '../../types/combat'

// CSS keyframes 注入（只注一次）
const STYLE_ID = 'combat-float-up'
if (typeof document !== 'undefined' && !document.getElementById(STYLE_ID)) {
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = `
    @keyframes floatUp {
      0%   { opacity: 1; transform: translateY(0);    }
      80%  { opacity: 1; transform: translateY(-60px); }
      100% { opacity: 0; transform: translateY(-80px); }
    }
    .dmg-float { animation: floatUp 1.5s ease-out forwards; }
  `
  document.head.appendChild(style)
}

function DmgText({ dmg }: { dmg: DamageNumber }) {
  const removeDamageNumber = useCombatStore((s) => s.removeDamageNumber)
  const { id, value, kind } = dmg

  // 随机水平位置（seed from id）
  const seed = parseInt(id, 10)
  const leftPct = 30 + ((seed * 37) % 40)  // 30% – 70%
  // 怪物在上方（20-30vh），玩家在下方（65-75vh）
  const topPct = kind === 'damage' && dmg.target_id !== 'player'
    ? 20 + ((seed * 13) % 10)
    : 65 + ((seed * 17) % 10)

  const colorClass =
    kind === 'damage' ? 'text-red-400' :
    kind === 'heal'   ? 'text-green-400' :
    'text-gray-400'

  const label =
    kind === 'miss'   ? 'MISS' :
    kind === 'heal'   ? `+${value}` :
    `-${value}`

  return (
    <div
      className={`dmg-float absolute font-bold text-xl pointer-events-none select-none ${colorClass}`}
      style={{ left: `${leftPct}%`, top: `${topPct}vh` }}
      onAnimationEnd={() => removeDamageNumber(id)}
    >
      {label}
    </div>
  )
}

export default function DamageNumberLayer() {
  const damageNumbers = useCombatStore((s) => s.damageNumbers)

  if (damageNumbers.length === 0) return null

  return (
    <div className="absolute inset-0 z-40 pointer-events-none overflow-hidden">
      {damageNumbers.map((dmg) => (
        <DmgText key={dmg.id} dmg={dmg} />
      ))}
    </div>
  )
}
