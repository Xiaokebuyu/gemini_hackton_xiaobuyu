import { useOverlayStore } from '../../stores/overlayStore'
import type { StructuredActionRequest } from '../../types/api'

interface BoardEntry {
  quest_id: string
  title: string
  content: string
  quest_status: string
}

interface BoardData {
  board_id: string
  entries: BoardEntry[]
}

interface Props {
  sendAction: (req: StructuredActionRequest) => void
}

export default function BoardOverlay({ sendAction }: Props) {
  const overlay = useOverlayStore()
  const data = overlay.data as BoardData

  if (!data?.board_id) return null

  const doAction = (actionType: string, questId: string) => {
    overlay.close()
    sendAction({
      action_type: actionType,
      params: { board_id: data.board_id, quest_id: questId },
    })
  }

  const available = (data.entries ?? []).filter((e) => e.quest_status === 'available')
  const active = (data.entries ?? []).filter((e) => e.quest_status === 'active')

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-96 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-amber-400 font-bold">委托板</h2>
          <button
            onClick={overlay.close}
            className="text-gray-500 hover:text-gray-300 text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* 可接取 */}
          {available.length > 0 && (
            <div>
              <p className="text-gray-500 text-xs mb-2">── 可接取委托 ──</p>
              {available.map((entry) => (
                <div key={entry.quest_id} className="mb-3 p-3 bg-gray-800/60 rounded-lg">
                  <p className="text-amber-300 text-sm font-medium mb-1">{entry.title}</p>
                  <p className="text-gray-400 text-xs mb-2 leading-relaxed">{entry.content}</p>
                  <button
                    onClick={() => doAction('board_accept_quest', entry.quest_id)}
                    className="text-xs text-sky-400 hover:text-sky-300 border border-sky-700/50 rounded px-3 py-1"
                  >
                    接受委托
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* 进行中 */}
          {active.length > 0 && (
            <div>
              <p className="text-gray-500 text-xs mb-2">── 进行中 ──</p>
              {active.map((entry) => (
                <div key={entry.quest_id} className="mb-3 p-3 bg-gray-800/60 rounded-lg">
                  <p className="text-amber-300 text-sm font-medium mb-1">{entry.title}</p>
                  <div className="flex gap-2 mt-2">
                    <button
                      onClick={() => doAction('board_complete_quest', entry.quest_id)}
                      className="text-xs text-green-400 hover:text-green-300 border border-green-700/50 rounded px-3 py-1"
                    >
                      完成
                    </button>
                    <button
                      onClick={() => doAction('board_retire_quest', entry.quest_id)}
                      className="text-xs text-red-400 hover:text-red-300 border border-red-700/50 rounded px-3 py-1"
                    >
                      撤销
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {available.length === 0 && active.length === 0 && (
            <p className="text-gray-500 text-sm text-center py-4">暂无委托</p>
          )}
        </div>
      </div>
    </div>
  )
}
