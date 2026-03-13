import { useCallback, useEffect, useState } from 'react'
import { getCharacter } from '../../lib/api'
import { useOverlayStore } from '../../stores/overlayStore'
import { usePlayerStore } from '../../stores/playerStore'
import { useSessionStore } from '../../stores/sessionStore'
import type { StructuredActionRequest, CharacterPanelData } from '../../types/api'

const STAT_CONFIG = [
  { key: 'str', short: 'STR', label: '力量', aliases: ['strength'] },
  { key: 'dex', short: 'DEX', label: '敏捷', aliases: ['dexterity'] },
  { key: 'con', short: 'CON', label: '体质', aliases: ['constitution'] },
  { key: 'int', short: 'INT', label: '智力', aliases: ['intelligence'] },
  { key: 'wis', short: 'WIS', label: '感知', aliases: ['wisdom'] },
  { key: 'cha', short: 'CHA', label: '魅力', aliases: ['charisma'] },
] as const

interface Props {
  sendAction: (req: StructuredActionRequest) => Promise<void> | void
}

function featureLabel(feature: string | { name: string; description?: string }): string {
  return typeof feature === 'string' ? feature : feature.name
}

function getStatValue(stats: Record<string, number>, key: string, aliases: readonly string[]): number | null {
  const direct = stats[key]
  if (typeof direct === 'number') return direct
  for (const alias of aliases) {
    const value = stats[alias]
    if (typeof value === 'number') return value
  }
  return null
}

export default function CharacterPanel({ sendAction }: Props) {
  const { close } = useOverlayStore()
  const { worldId, sessionId } = useSessionStore()
  const updatePlayer = usePlayerStore((s) => s.updateFromPanel)
  const [data, setData] = useState<CharacterPanelData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [pendingAsi, setPendingAsi] = useState<string | null>(null)

  const loadCharacter = useCallback(async () => {
    if (!worldId || !sessionId) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const response = await getCharacter(worldId, sessionId)
      setData(response.player)
      updatePlayer(response)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [worldId, sessionId, updatePlayer])

  useEffect(() => {
    void loadCharacter()
  }, [loadCharacter])

  const handleApplyAsi = useCallback(async (stat: string, bonus: number) => {
    if (!data) return
    setPendingAsi(`${stat}:${bonus}`)
    setError(null)
    try {
      await sendAction({
        action_type: 'apply_asi',
        params: { stat, bonus },
      })
      await loadCharacter()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setPendingAsi(null)
    }
  }, [data, loadCharacter, sendAction])

  const remainingAsiPoints = typeof data?.asi_points_remaining === 'number'
    ? data.asi_points_remaining
    : 0

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-gray-950/90">
      <div className="max-h-[80vh] w-96 overflow-y-auto rounded-xl border border-gray-700 bg-gray-900">
        <div className="flex items-center justify-between border-b border-gray-700 px-5 py-3">
          <h2 className="font-bold text-amber-400">角色面板</h2>
          <button
            onClick={close}
            className="text-lg leading-none text-gray-500 hover:text-gray-300"
          >
            ×
          </button>
        </div>

        {error ? (
          <p className="p-5 text-sm text-red-400">{error}</p>
        ) : loading || !data ? (
          <p className="p-5 text-center text-sm text-gray-500">加载中…</p>
        ) : (
          <div className="space-y-4 p-5">
            <div>
              <p className="text-lg font-semibold text-gray-100">{data.character_name}</p>
              <p className="text-sm text-amber-300">{data.character_class} · Lv.{data.level}</p>
              <p className="mt-0.5 text-xs text-gray-400">XP: {data.xp}</p>
            </div>

            <div className="flex gap-4 text-sm">
              <span className="text-red-400">HP {data.hp}/{data.max_hp}</span>
              <span className="text-blue-300">AC {data.ac}</span>
              <span className="text-yellow-400">{data.gold}G</span>
            </div>

            {remainingAsiPoints > 0 && (
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-amber-300">属性分配</p>
                  <span className="text-xs text-amber-200">剩余 {remainingAsiPoints} 点</span>
                </div>
                <p className="mt-1 text-xs text-gray-300">
                  每项属性上限 20。可拆成两次 `+1`，或一次 `+2`。
                </p>
              </div>
            )}

            <div>
              <p className="mb-1.5 text-xs text-gray-500">── 属性 ──</p>
              <div className="space-y-2">
                {STAT_CONFIG.map(({ key, short, label, aliases }) => {
                  const value = getStatValue(data.stats, key, aliases)
                  const canPlusOne = value !== null && value < 20 && remainingAsiPoints >= 1
                  const canPlusTwo = value !== null && value <= 18 && remainingAsiPoints >= 2
                  return (
                    <div
                      key={key}
                      className="flex items-center justify-between rounded bg-gray-800 px-3 py-2"
                    >
                      <div>
                        <span className="text-xs text-gray-400">{label}</span>
                        <span className="ml-2 font-mono text-sm text-gray-100">
                          {value ?? '—'}
                        </span>
                        <span className="ml-2 text-[11px] text-gray-500">{short}</span>
                      </div>
                      {remainingAsiPoints > 0 && (
                        <div className="flex gap-2">
                          <button
                            onClick={() => void handleApplyAsi(key, 1)}
                            disabled={!canPlusOne || pendingAsi !== null}
                            className="rounded border border-amber-600/40 px-2 py-1 text-xs text-amber-300 disabled:cursor-not-allowed disabled:border-gray-700 disabled:text-gray-600"
                          >
                            +1
                          </button>
                          <button
                            onClick={() => void handleApplyAsi(key, 2)}
                            disabled={!canPlusTwo || pendingAsi !== null}
                            className="rounded border border-amber-600/40 px-2 py-1 text-xs text-amber-300 disabled:cursor-not-allowed disabled:border-gray-700 disabled:text-gray-600"
                          >
                            +2
                          </button>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            {data.class_features?.length > 0 && (
              <div>
                <p className="mb-1.5 text-xs text-gray-500">── 职业特性 ──</p>
                <ul className="space-y-0.5">
                  {data.class_features.map((feature, index) => (
                    <li key={index} className="text-sm text-gray-300">
                      · {featureLabel(feature)}
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
