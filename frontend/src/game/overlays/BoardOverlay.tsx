import { useOverlayStore } from '../../stores/overlayStore'
import type { BoardSnapshotData } from '../../types/sse'
import type { InteractRequest } from '../../types/api'

interface Props {
  sendInteract: (req: InteractRequest) => void
}

const STATUS_ICON: Record<string, string> = {
  active: '⚔',
  completed: '✅',
}

const STATUS_LABEL: Record<string, string> = {
  active: '进行中',
  completed: '已完成',
}

export default function BoardOverlay({ sendInteract }: Props) {
  const overlay = useOverlayStore()
  const data = overlay.data as BoardSnapshotData

  if (!data?.target_id) return null

  const doAccept = (questId: string) => {
    overlay.close()
    sendInteract({
      intent: 'accept',
      target_kind: 'board',
      target_id: data.target_id,
      quest_id: questId,
    })
  }

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-80 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-amber-400 font-bold">布告栏</h2>
          <button
            onClick={overlay.close}
            className="text-gray-500 hover:text-gray-300 text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-3">
          {data.entries.length === 0 && (
            <p className="text-gray-500 text-sm text-center">布告栏暂无任务</p>
          )}

          {data.entries.map((entry) => {
            const status = entry.quest_status ?? ''
            const canAccept =
              status !== 'active' && status !== 'completed' && !!entry.quest_id

            return (
              <div
                key={entry.board_id}
                className="border-b border-gray-800 pb-3 last:border-0 last:pb-0"
              >
                <div className="flex items-start justify-between gap-2">
                  <p className="text-gray-200 text-sm">
                    {STATUS_ICON[status] ?? '📋'} {entry.title}
                  </p>
                  {canAccept ? (
                    <button
                      onClick={() => doAccept(entry.quest_id!)}
                      className="flex-shrink-0 text-xs text-sky-400 hover:text-sky-300 border border-sky-700/50 rounded px-2 py-0.5"
                    >
                      接受
                    </button>
                  ) : (
                    STATUS_LABEL[status] && (
                      <span className="flex-shrink-0 text-xs text-gray-500">
                        {STATUS_LABEL[status]}
                      </span>
                    )
                  )}
                </div>
                {entry.content && (
                  <p className="text-gray-400 text-xs mt-1 ml-5 line-clamp-3">
                    {entry.content}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
