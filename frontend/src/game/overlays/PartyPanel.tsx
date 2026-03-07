import { useOverlayStore } from '../../stores/overlayStore'
import { usePartyStore } from '../../stores/partyStore'

interface PartyPanelProps {
  sendCompanionDismiss: (npcId: string) => void
}

export default function PartyPanel({ sendCompanionDismiss }: PartyPanelProps) {
  const { close } = useOverlayStore()
  const members = usePartyStore((s) => s.members)
  const memberList = Object.values(members)

  const handleDismiss = (npcId: string) => {
    sendCompanionDismiss(npcId)
  }

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center">
      <div className="bg-gray-900 border border-gray-700 rounded-xl w-80 max-h-[80vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-700">
          <h2 className="text-amber-400 font-bold">队伍</h2>
          <button
            onClick={close}
            className="text-gray-500 hover:text-gray-300 text-lg leading-none"
          >
            ×
          </button>
        </div>

        {memberList.length === 0 ? (
          <p className="p-5 text-gray-500 text-sm text-center">队伍中暂无队友</p>
        ) : (
          <div className="p-4 space-y-3">
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
                  onClick={() => handleDismiss(m.id)}
                  className="text-red-400 hover:text-red-300 text-xs px-2 py-1 border border-red-400/40 hover:border-red-300/60 rounded transition-colors"
                >
                  解散
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
