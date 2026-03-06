import { useEffect, useState } from 'react'
import { useOverlayStore } from '../../stores/overlayStore'
import { useSessionStore } from '../../stores/sessionStore'
import { getInventory } from '../../lib/api'
import type { InventoryItem, InventoryPanelData } from '../../types/api'

const SLOT_LABELS: [string, string][] = [
  ['head', '头'],
  ['chest', '胸'],
  ['gloves', '手'],
  ['boots', '脚'],
  ['cloak', '披'],
  ['amulet', '坠'],
  ['ring_l', '戒L'],
  ['ring_r', '戒R'],
  ['main_hand', '主手'],
  ['off_hand', '副手'],
  ['ranged', '远程'],
  ['ammo', '弹药'],
  ['belt', '腰带'],
]

function itemName(item: InventoryItem): string {
  return String(item.name ?? item.item_id ?? '?')
}

export default function InventoryPanel() {
  const { close } = useOverlayStore()
  const { worldId, sessionId } = useSessionStore()
  const [data, setData] = useState<InventoryPanelData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!worldId || !sessionId) return
    getInventory(worldId, sessionId)
      .then(setData)
      .catch((err: Error) => setError(err.message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const isEmpty =
    data &&
    data.inventory.length === 0 &&
    SLOT_LABELS.every(([slot]) => !data.equipment[slot])

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-80 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-amber-400 font-bold">背包</h2>
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
            <p className="text-yellow-400 text-sm">💰 {data.gold}G</p>

            <div>
              <p className="text-gray-500 text-xs mb-1.5">── 已装备 ──</p>
              <div className="grid grid-cols-2 gap-1 text-sm">
                {SLOT_LABELS.map(([slot, label]) => {
                  const item = data.equipment[slot]
                  return (
                    <div key={slot} className="bg-gray-800 rounded px-2 py-1 flex gap-1.5">
                      <span className="text-gray-500 text-xs flex-shrink-0">[{label}]</span>
                      <span className="text-gray-300 truncate text-xs">
                        {item ? itemName(item) : '—'}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            {data.inventory.length > 0 && (
              <div>
                <p className="text-gray-500 text-xs mb-1.5">── 背包物品 ──</p>
                {data.inventory.map((item, i) => (
                  <div
                    key={item.item_id ?? i}
                    className="flex items-center justify-between py-1"
                  >
                    <span className="text-gray-300 text-sm">{itemName(item)}</span>
                    <div className="flex items-center gap-2">
                      {item.type && (
                        <span className="text-gray-500 text-xs">{String(item.type)}</span>
                      )}
                      {item.count > 1 && (
                        <span className="text-gray-400 text-xs">x{item.count}</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}

            {isEmpty && (
              <p className="text-gray-500 text-sm text-center">背包是空的</p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
