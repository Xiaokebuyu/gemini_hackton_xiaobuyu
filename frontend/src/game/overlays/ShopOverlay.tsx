import { useOverlayStore } from '../../stores/overlayStore'
import type { ShopSnapshotData, ShopServiceItem } from '../../types/sse'
import type { InteractRequest } from '../../types/api'

interface Props {
  sendInteract: (req: InteractRequest) => void
}

export default function ShopOverlay({ sendInteract }: Props) {
  const overlay = useOverlayStore()
  const data = overlay.data as ShopSnapshotData

  if (!data?.npc_id) return null

  // 先关面板，再触发交互，SSE 响应不会被 overlay 遮挡
  const doInteract = (req: InteractRequest) => {
    overlay.close()
    sendInteract(req)
  }

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-80 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-amber-400 font-bold">商店</h2>
          <button
            onClick={overlay.close}
            className="text-gray-500 hover:text-gray-300 text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-4">
          <p className="text-yellow-400 text-sm">💰 持有：{data.player_gold}G</p>

          {/* 可购买 */}
          {data.stock.length > 0 && (
            <div>
              <p className="text-gray-500 text-xs mb-1.5">── 可购买 ──</p>
              {data.stock.map((item) => (
                <div key={item.item_id} className="flex items-center justify-between py-1">
                  <span className="text-gray-300 text-sm">{item.name}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-yellow-400 text-xs">{item.base_price}G</span>
                    <button
                      onClick={() =>
                        doInteract({
                          intent: 'buy',
                          target_kind: 'npc',
                          target_id: data.npc_id,
                          item_id: item.item_id,
                          count: 1,
                        })
                      }
                      disabled={data.player_gold < item.base_price}
                      className="text-xs text-sky-400 hover:text-sky-300 disabled:opacity-40 border border-sky-700/50 rounded px-2 py-0.5"
                    >
                      购买
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 可出售 */}
          {data.player_sellable_items.length > 0 && (
            <div>
              <p className="text-gray-500 text-xs mb-1.5">── 可出售 ──</p>
              {data.player_sellable_items.map((item) => (
                <div key={item.item_id} className="flex items-center justify-between py-1">
                  <span className="text-gray-300 text-sm">{item.name}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-yellow-400 text-xs">{item.base_price}G</span>
                    <button
                      onClick={() =>
                        doInteract({
                          intent: 'sell',
                          target_kind: 'npc',
                          target_id: data.npc_id,
                          item_id: item.item_id,
                          count: 1,
                        })
                      }
                      className="text-xs text-green-400 hover:text-green-300 border border-green-700/50 rounded px-2 py-0.5"
                    >
                      出售
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 可用服务 */}
          {data.services && data.services.length > 0 && (
            <div>
              <p className="text-gray-500 text-xs mb-1.5">── 可用服务 ──</p>
              {data.services.map((service: ShopServiceItem) => (
                <div key={service.service_id} className="py-1.5 border-b border-gray-800 last:border-0">
                  <div className="flex items-center justify-between">
                    <span className="text-purple-300 text-sm">✨ {service.label}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-yellow-400 text-xs">{service.price}G</span>
                      <button
                        onClick={() =>
                          doInteract({
                            intent: 'buy_service',
                            target_kind: 'npc',
                            target_id: data.npc_id,
                            item_id: service.service_id,
                          })
                        }
                        disabled={data.player_gold < service.price}
                        className="text-xs text-purple-400 hover:text-purple-300 disabled:opacity-40 border border-purple-700/50 rounded px-2 py-0.5"
                      >
                        使用
                      </button>
                    </div>
                  </div>
                  {service.notes && (
                    <p className="text-gray-500 text-xs mt-0.5 pl-4">{service.notes}</p>
                  )}
                  {service.effects_summary && (
                    <p className="text-emerald-600 text-xs mt-0.5 pl-4">{service.effects_summary}</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {data.stock.length === 0 && data.player_sellable_items.length === 0 && (!data.services || data.services.length === 0) && (
            <p className="text-gray-500 text-sm text-center">商店暂无商品</p>
          )}
        </div>
      </div>
    </div>
  )
}
