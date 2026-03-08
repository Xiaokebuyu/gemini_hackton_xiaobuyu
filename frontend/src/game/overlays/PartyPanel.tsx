import { useMemo } from 'react'
import { useOverlayStore } from '../../stores/overlayStore'
import { usePartyStore } from '../../stores/partyStore'
import { useSceneStore } from '../../stores/sceneStore'

interface PartyPanelProps {
  sendCompanionRecruit: (npcId: string) => void
  sendCompanionDismiss: (npcId: string) => void
}

const ROLE_LABEL: Record<string, string> = {
  main: '核心角色',
  secondary: '重要角色',
  passerby: '路人',
  companion: '队友',
}

const SOURCE_LABEL: Record<string, string> = {
  resident: '常驻',
  schedule: '日程到场',
  planner: '叙事投递',
  companion: '队伍跟随',
  event: '事件出现',
}

export default function PartyPanel({ sendCompanionRecruit, sendCompanionDismiss }: PartyPanelProps) {
  const { close } = useOverlayStore()
  const members = usePartyStore((s) => s.members)
  const presentNpcs = useSceneStore((s) => s.presentNpcs)

  const memberList = useMemo(() => Object.values(members), [members])
  const candidateList = useMemo(() => presentNpcs.filter((npc) => (
    npc.recruitable
      && !members[npc.character_id]
      && !npc.is_companion
      && npc.role !== 'companion'
  )), [members, presentNpcs])

  const describeCandidate = (npc: (typeof candidateList)[number]) => {
    const parts = [ROLE_LABEL[npc.role] ?? npc.role]
    if (npc.presence_source) {
      parts.push(SOURCE_LABEL[npc.presence_source] ?? npc.presence_source)
    }
    const firstTag = npc.tags[0]
    if (firstTag) {
      parts.push(firstTag)
    }
    return parts.join(' · ')
  }

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-[28rem] max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-amber-400 font-bold">队伍</h2>
          <button
            onClick={close}
            className="text-gray-500 hover:text-gray-300 text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-4 space-y-6">
          <section className="space-y-3">
            <div>
              <h3 className="text-sm font-semibold text-gray-200">当前成员</h3>
              <p className="text-xs text-gray-500 mt-1">审批值以后端为准，解散会立即同步队伍状态。</p>
            </div>

            {memberList.length === 0 ? (
              <p className="text-gray-500 text-sm text-center py-3">队伍中暂无队友</p>
            ) : (
              <div className="space-y-3">
                {memberList.map((m) => (
                  <div
                    key={m.id}
                    className="bg-gray-800 rounded-lg px-4 py-3 flex items-center justify-between"
                  >
                    <div>
                      <p className="text-gray-100 font-semibold">
                        {m.name || m.id}
                      </p>
                      <div className="flex gap-3 text-xs mt-1">
                        {m.classId && (
                          <span className="text-amber-300">{m.classId}</span>
                        )}
                        <span className="text-emerald-400">好感: {m.approval}</span>
                      </div>
                    </div>
                    <button
                      onClick={() => sendCompanionDismiss(m.id)}
                      className="text-red-400 hover:text-red-300 text-xs px-2 py-1 border border-red-400/40 hover:border-red-300/60 rounded transition-colors"
                    >
                      解散
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="space-y-3 border-t border-gray-800 pt-4">
            <div>
              <h3 className="text-sm font-semibold text-gray-200">当前场景可邀请对象</h3>
              <p className="text-xs text-gray-500 mt-1">这里只展示当前在场且数据层标记为可招募的角色。是否同意入队仍以后端关系与内容规则为准。</p>
            </div>

            {candidateList.length === 0 ? (
              <p className="text-gray-500 text-sm text-center py-3">当前场景暂无可邀请对象</p>
            ) : (
              <div className="space-y-3">
                {candidateList.map((npc) => (
                  <div
                    key={npc.character_id}
                    className="bg-gray-800 rounded-lg px-4 py-3 flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0">
                      <p className="text-gray-100 font-semibold truncate">
                        {npc.name}
                      </p>
                      <p className="text-xs text-gray-400 mt-1 truncate">
                        {describeCandidate(npc)}
                      </p>
                    </div>
                    <button
                      onClick={() => sendCompanionRecruit(npc.character_id)}
                      className="text-emerald-300 hover:text-emerald-200 text-xs px-2 py-1 border border-emerald-400/40 hover:border-emerald-300/60 rounded transition-colors whitespace-nowrap"
                    >
                      邀请入队
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <section className="border-t border-gray-800 pt-4">
            <p className="text-xs text-gray-500 leading-5">
              明确邀请时，部分角色也会在对话里直接调用入队或离队工具。这里的列表只是显式入口，不替代对话判定。
            </p>
          </section>
        </div>
      </div>
    </div>
  )
}
