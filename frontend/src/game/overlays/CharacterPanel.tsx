import { useEffect, useState } from 'react'
import { useOverlayStore } from '../../stores/overlayStore'
import { useSessionStore } from '../../stores/sessionStore'
import { getCharacter } from '../../lib/api'
import type { CharacterPanelData } from '../../types/api'

const STAT_LABELS: [string, string][] = [
  ['STR', '力量'], ['DEX', '敏捷'], ['CON', '体质'],
  ['INT', '智力'], ['WIS', '感知'], ['CHA', '魅力'],
]

const FULL_NAMES: Record<string, string> = {
  STR: 'strength', DEX: 'dexterity', CON: 'constitution',
  INT: 'intelligence', WIS: 'wisdom', CHA: 'charisma',
}

function getStat(stats: Record<string, number>, key: string): number | string {
  return stats[key] ?? stats[FULL_NAMES[key]] ?? '—'
}

function featureLabel(f: string | { name: string; description?: string }): string {
  return typeof f === 'string' ? f : f.name
}

export default function CharacterPanel() {
  const { close } = useOverlayStore()
  const { worldId, sessionId } = useSessionStore()
  const [data, setData] = useState<CharacterPanelData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!worldId || !sessionId) return
    getCharacter(worldId, sessionId)
      .then((res) => setData(res.player))
      .catch((err: Error) => setError(err.message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-80 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-amber-400 font-bold">角色面板</h2>
          <button
            onClick={close}
            className="text-gray-500 hover:text-gray-300 text-lg leading-none"
          >
            ×
          </button>
        </div>

        {error ? (
          <p className="p-5 text-red-400 text-sm">{error}</p>
        ) : !data ? (
          <p className="p-5 text-gray-500 text-sm text-center">加载中…</p>
        ) : (
          <div className="p-5 space-y-4">
            {/* 基础信息 */}
            <div>
              <p className="text-gray-100 font-semibold text-lg">{data.character_name}</p>
              <p className="text-amber-300 text-sm">{data.character_class} · Lv.{data.level}</p>
              <p className="text-gray-400 text-xs mt-0.5">XP: {data.xp}</p>
            </div>

            {/* 状态栏 */}
            <div className="flex gap-4 text-sm">
              <span className="text-red-400">❤ {data.hp}/{data.max_hp}</span>
              <span className="text-blue-300">🛡 AC {data.ac}</span>
              <span className="text-yellow-400">💰 {data.gold}G</span>
            </div>

            {/* 属性 */}
            <div>
              <p className="text-gray-500 text-xs mb-1.5">── 属性 ──</p>
              <div className="grid grid-cols-3 gap-1 text-sm">
                {STAT_LABELS.map(([key, label]) => (
                  <div key={key} className="bg-gray-800 rounded px-2 py-1">
                    <span className="text-gray-400 text-xs">{label}</span>
                    <span className="text-gray-100 ml-1 font-mono">{getStat(data.stats, key)}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* 职业特性 */}
            {data.class_features?.length > 0 && (
              <div>
                <p className="text-gray-500 text-xs mb-1.5">── 职业特性 ──</p>
                <ul className="space-y-0.5">
                  {data.class_features.map((f, i) => (
                    <li key={i} className="text-gray-300 text-sm">
                      · {featureLabel(f)}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
