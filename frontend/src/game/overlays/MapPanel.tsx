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
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-80 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-amber-400 font-bold">地图</h2>
          <button
            onClick={overlay.close}
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
            <p className="text-gray-300 text-sm">
              当前：<span className="text-amber-300">{currentArea?.name ?? data.current_area}</span>
              {data.current_location && (
                <span className="text-gray-400"> · {data.current_location}</span>
              )}
            </p>

            {/* 当前区域子地点 */}
            {currentArea && currentArea.sub_locations.length > 0 && (
              <div>
                <p className="text-gray-500 text-xs mb-1.5">── 区域内地点 ──</p>
                {currentArea.sub_locations.map((loc) => (
                  <div key={loc.id} className="flex items-center justify-between py-1">
                    <span className="text-gray-300 text-sm">{loc.name}</span>
                    {loc.id === data.current_location ? (
                      <span className="text-amber-400 text-xs">当前</span>
                    ) : (
                      <button
                        onClick={() =>
                          nav({ action: 'enter_sub_location', location_id: loc.id })
                        }
                        className="text-xs text-sky-400 hover:text-sky-300 border border-sky-700/50 rounded px-2 py-0.5"
                      >
                        进入
                      </button>
                    )}
                  </div>
                ))}
                {data.current_location && (
                  <button
                    onClick={() => nav({ action: 'leave_sub_location' })}
                    className="text-xs text-gray-400 hover:text-gray-300 mt-1"
                  >
                    ← 离开当前地点
                  </button>
                )}
              </div>
            )}

            {/* 可前往区域 */}
            {otherAreas.length > 0 && (
              <div>
                <p className="text-gray-500 text-xs mb-1.5">── 可前往区域 ──</p>
                {otherAreas.map((area) => (
                  <div key={area.id} className="flex items-center justify-between py-1">
                    <span className="text-gray-300 text-sm">
                      {area.name}
                      {area.danger_level !== null && area.danger_level > 0.6 && (
                        <span className="text-red-400 text-xs ml-1">危险!</span>
                      )}
                    </span>
                    <button
                      onClick={() => nav({ action: 'move_area', area_id: area.id })}
                      className="text-xs text-sky-400 hover:text-sky-300 border border-sky-700/50 rounded px-2 py-0.5"
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
