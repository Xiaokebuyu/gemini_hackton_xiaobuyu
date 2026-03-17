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
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center">
      <div className="panel-ornate texture-noise w-80 max-h-[80vh] overflow-y-auto">
        <div className="panel-header flex items-center justify-between">
          <h2 className="panel-title">商店</h2>
          <button
            onClick={overlay.close}
            className="text-parchment-500 hover:text-gold-400 transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-4">
          <p className="text-gold-300 font-display text-sm">💰 持有：{data.player_gold}G</p>

          {/* 可购买 */}
          {data.stock.length > 0 && (
            <div>
              <p className="font-display text-xs text-parchment-500 tracking-wider mb-1.5">可购买</p>
              <hr className="divider-subtle" />
              {data.stock.map((item) => (
                <div key={item.item_id} className="panel-inset px-3 py-2 mb-1.5 flex items-center justify-between">
                  <span className="text-parchment-200 text-sm">{item.name}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-gold-400 text-xs font-display">{item.base_price}G</span>
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
                      className="btn-fantasy text-xs"
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
              <p className="font-display text-xs text-parchment-500 tracking-wider mb-1.5">可出售</p>
              <hr className="divider-subtle" />
              {data.player_sellable_items.map((item) => (
                <div key={item.item_id} className="panel-inset px-3 py-2 mb-1.5 flex items-center justify-between">
                  <span className="text-parchment-200 text-sm">{item.name}</span>
                  <div className="flex items-center gap-2">
                    <span className="text-gold-400 text-xs font-display">{item.base_price}G</span>
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
                      className="btn-fantasy text-xs !border-emerald-600/30"
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
              <p className="font-display text-xs text-parchment-500 tracking-wider mb-1.5">可用服务</p>
              <hr className="divider-subtle" />
              {data.services.map((service: ShopServiceItem) => (
                <div key={service.service_id} className="panel-inset px-3 py-2 mb-1.5">
                  <div className="flex items-center justify-between">
                    <span className="text-parchment-200 text-sm">✨ {service.label}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-gold-400 text-xs font-display">{service.price}G</span>
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
                        className="btn-fantasy text-xs !border-purple-500/30"
                      >
                        使用
                      </button>
                    </div>
                  </div>
                  {service.notes && (
                    <p className="text-parchment-500 text-xs mt-0.5 pl-4">{service.notes}</p>
                  )}
                  {service.effects_summary && (
                    <p className="text-emerald-600 text-xs mt-0.5 pl-4">{service.effects_summary}</p>
                  )}
                </div>
              ))}
            </div>
          )}

          {data.stock.length === 0 && data.player_sellable_items.length === 0 && (!data.services || data.services.length === 0) && (
            <p className="text-parchment-500 text-sm text-center">商店暂无商品</p>
          )}
        </div>
      </div>
    </div>
  )
}
