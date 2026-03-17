import { useEffect, useState } from 'react'
import { useOverlayStore } from '../../stores/overlayStore'
import { useSessionStore } from '../../stores/sessionStore'
import { getMap } from '../../lib/api'
import type { MapPanelData, NavigateRequest } from '../../types/api'

interface Props {
  sendNavigate: (req: NavigateRequest) => void
}

export default function MapPanel({ sendNavigate }: Props) {
  const overlay = useOverlayStore()
  const { worldId, sessionId } = useSessionStore()
  const [data, setData] = useState<MapPanelData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!worldId || !sessionId) return
    getMap(worldId, sessionId)
      .then(setData)
      .catch((err: Error) => setError(err.message))
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // 先关面板，再触发导航，防止 overlay 残留于转场过程
  const nav = (req: NavigateRequest) => {
    overlay.close()
    sendNavigate(req)
  }

  const currentArea = data?.areas.find((a) => a.id === data.current_area)
  const otherAreas =
    data?.areas.filter(
      (a) => a.id !== data.current_area && data.discovered_area_ids.includes(a.id),
    ) ?? []

  return (
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center">
      <div className="panel-ornate texture-noise w-80 max-h-[80vh] overflow-y-auto">
        <div className="panel-header flex items-center justify-between">
          <h2 className="panel-title">地图</h2>
          <button
            onClick={overlay.close}
            className="text-parchment-500 hover:text-gold-400 transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>

        {error ? (
          <p className="p-5 text-red-400 text-sm">{error}</p>
        ) : !data ? (
          <p className="p-5 text-parchment-500 text-sm text-center">加载中…</p>
        ) : (
          <div className="p-5 space-y-4">
            <p className="text-parchment-300 text-sm">
              当前：<span className="font-display text-gold-300">{currentArea?.name ?? data.current_area}</span>
              {data.current_location && (
                <span className="text-parchment-500"> · {data.current_location}</span>
              )}
              {currentArea && (
                <span className="badge-fantasy ml-2">当前位置</span>
              )}
            </p>

            {/* 当前区域子地点 */}
            {currentArea && currentArea.sub_locations.length > 0 && (
              <div>
                <p className="font-display text-xs text-gold-400/70 tracking-wider uppercase mb-1.5">区域内地点</p>
                <hr className="divider-subtle" />
                {currentArea.sub_locations.map((loc) => (
                  <div key={loc.id} className="flex items-center justify-between py-1 mt-1">
                    <span className="font-display text-gold-300 text-sm">{loc.name}</span>
                    {loc.id === data.current_location ? (
                      <span className="badge-fantasy">当前</span>
                    ) : (
                      <button
                        onClick={() =>
                          nav({ action: 'enter_sub_location', location_id: loc.id })
                        }
                        className="btn-fantasy text-xs"
                      >
                        进入
                      </button>
                    )}
                  </div>
                ))}
                {data.current_location && (
                  <button
                    onClick={() => nav({ action: 'leave_sub_location' })}
                    className="btn-subtle text-xs mt-1"
                  >
                    ← 离开当前地点
                  </button>
                )}
              </div>
            )}

            {/* 可前往区域 */}
            {otherAreas.length > 0 && (
              <div>
                <p className="font-display text-xs text-gold-400/70 tracking-wider uppercase mb-1.5">可前往区域</p>
                <hr className="divider-subtle" />
                {otherAreas.map((area) => (
                  <div key={area.id} className="flex items-center justify-between py-1 mt-1">
                    <span className="font-display text-gold-300 text-sm">
                      {area.name}
                      {area.danger_level !== null && area.danger_level > 0.6 && (
                        <span className="text-red-400 text-xs ml-1">危险!</span>
                      )}
                    </span>
                    <button
                      onClick={() => nav({ action: 'move_area', area_id: area.id })}
                      className="btn-fantasy text-xs"
                    >
                      移动
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
