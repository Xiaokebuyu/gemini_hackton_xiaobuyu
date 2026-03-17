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
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center">
      <div className="panel-ornate texture-noise w-[28rem] max-h-[80vh] overflow-y-auto">
        <div className="panel-header flex items-center justify-between">
          <h2 className="panel-title">队伍</h2>
          <button
            onClick={close}
            className="text-parchment-500 hover:text-gold-400 transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="p-4 space-y-6">
          <section className="space-y-3">
            <div>
              <h3 className="font-display text-xs text-gold-400/70 tracking-wider uppercase">当前成员</h3>
              <p className="text-xs text-parchment-500 mt-1">审批值以后端为准，解散会立即同步队伍状态。</p>
            </div>

            {memberList.length === 0 ? (
              <p className="text-parchment-500 text-sm text-center py-3">队伍中暂无队友</p>
            ) : (
              <div className="space-y-3">
                {memberList.map((m) => (
                  <div
                    key={m.id}
                    className="panel-inset px-4 py-3 flex items-center justify-between"
                  >
                    <div>
                      <p className="font-display text-gold-300">
                        {m.name || m.id}
                      </p>
                      <div className="flex gap-3 text-xs mt-1">
                        {m.classId && (
                          <span className="text-parchment-400">{m.classId}</span>
                        )}
                        <span className="text-emerald-400">
                          好感: <span className="font-display">{m.approval}</span>
                        </span>
                      </div>
                    </div>
                    <button
                      onClick={() => sendCompanionDismiss(m.id)}
                      className="btn-fantasy text-red-400 hover:text-red-300 text-xs px-2 py-1"
                    >
                      解散
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <hr className="divider-ornate" />

          <section className="space-y-3">
            <div>
              <h3 className="font-display text-xs text-gold-400/70 tracking-wider uppercase">当前场景可邀请对象</h3>
              <p className="text-xs text-parchment-500 mt-1">这里只展示当前在场且数据层标记为可招募的角色。是否同意入队仍以后端关系与内容规则为准。</p>
            </div>

            {candidateList.length === 0 ? (
              <p className="text-parchment-500 text-sm text-center py-3">当前场景暂无可邀请对象</p>
            ) : (
              <div className="space-y-3">
                {candidateList.map((npc) => (
                  <div
                    key={npc.character_id}
                    className="panel-inset px-4 py-3 flex items-center justify-between gap-3"
                  >
                    <div className="min-w-0">
                      <p className="font-display text-gold-300 truncate">
                        {npc.name}
                      </p>
                      <p className="text-xs text-parchment-400 mt-1 truncate">
                        {describeCandidate(npc)}
                      </p>
                    </div>
                    <button
                      onClick={() => sendCompanionRecruit(npc.character_id)}
                      className="btn-fantasy text-xs whitespace-nowrap"
                    >
                      邀请入队
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>

          <hr className="divider-subtle" />

          <section>
            <p className="text-xs text-parchment-500 leading-5">
              明确邀请时，部分角色也会在对话里直接调用入队或离队工具。这里的列表只是显式入口，不替代对话判定。
            </p>
          </section>
        </div>
      </div>
    </div>
  )
}
