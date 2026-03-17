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
  const reportable = (data.entries ?? []).filter((e) => e.quest_status === 'ready_to_report')

  return (
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center">
      <div className="panel-ornate texture-noise w-96 max-h-[80vh] overflow-y-auto">
        <div className="panel-header flex items-center justify-between">
          <h2 className="panel-title">委托板</h2>
          <button
            onClick={overlay.close}
            className="text-parchment-500 hover:text-gold-400 transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* 可接取 */}
          {available.length > 0 && (
            <div>
              <p className="font-display text-xs text-gold-400/70 tracking-wider uppercase mb-2">可接取委托</p>
              {available.map((entry) => (
                <div key={entry.quest_id} className="panel-inset px-3 py-2 mb-2">
                  <p className="font-display text-gold-300 text-sm mb-1">{entry.title}</p>
                  <p className="text-parchment-300 text-sm mb-2 leading-relaxed">{entry.content}</p>
                  <button
                    onClick={() => doAction('board_accept_quest', entry.quest_id)}
                    className="btn-fantasy text-xs"
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
              <p className="font-display text-xs text-gold-400/70 tracking-wider uppercase mb-2">进行中</p>
              {active.map((entry) => (
                <div key={entry.quest_id} className="panel-inset px-3 py-2 mb-2">
                  <p className="font-display text-gold-300 text-sm mb-1">{entry.title}</p>
                  <div className="flex gap-2 mt-2">
                    <button
                      onClick={() => doAction('board_retire_quest', entry.quest_id)}
                      className="btn-subtle text-xs text-red-400 hover:text-red-300"
                    >
                      放弃委托
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 可汇报 */}
          {reportable.length > 0 && (
            <div>
              <p className="font-display text-xs text-gold-400/70 tracking-wider uppercase mb-2">可汇报</p>
              {reportable.map((entry) => (
                <div key={entry.quest_id} className="panel-inset px-3 py-2 mb-2">
                  <p className="font-display text-gold-300 text-sm mb-1">{entry.title}</p>
                  <p className="text-parchment-300 text-xs mb-2">目标已达成，可领取报酬</p>
                  <button
                    onClick={() => doAction('board_complete_quest', entry.quest_id)}
                    className="btn-fantasy text-xs"
                  >
                    汇报完成
                  </button>
                </div>
              ))}
            </div>
          )}

          {available.length === 0 && active.length === 0 && reportable.length === 0 && (
            <p className="text-parchment-500 text-sm text-center py-4">暂无委托</p>
          )}
        </div>
      </div>
    </div>
  )
}
