import { useOverlayStore } from '../../stores/overlayStore'
import { useSceneStore } from '../../stores/sceneStore'
import type { NpcWantsToChatData } from '../../types/sse'
import type { PrivateChatRequest } from '../../types/api'

interface Props {
  sendPrivateChat: (req: PrivateChatRequest) => void
}

export default function ChatInviteModal({ sendPrivateChat }: Props) {
  const { data, close } = useOverlayStore()
  const invite = data as NpcWantsToChatData

  const handleAccept = () => {
    useSceneStore.getState().setGameMode('private_chat')
    close()
    sendPrivateChat({ npc_id: invite.npc_id, message: '' })
  }

  return (
    <div className="fixed bottom-48 left-1/2 -translate-x-1/2 z-30 bg-gray-900 border border-amber-700/50 rounded-xl shadow-xl p-4 w-72">
      <p className="text-gray-200 text-sm text-center mb-3">
        <span className="text-amber-300 font-bold">
          {invite?.npc_name ?? invite?.npc_id ?? '某人'}
        </span>{' '}
        想和你说些悄悄话...
      </p>
      <div className="flex gap-2">
        <button
          onClick={handleAccept}
          className="flex-1 bg-amber-600 hover:bg-amber-500 text-white py-1.5 rounded-lg text-sm transition-colors"
        >
          接受
        </button>
        <button
          onClick={close}
          className="flex-1 bg-gray-700 hover:bg-gray-600 text-gray-300 py-1.5 rounded-lg text-sm transition-colors"
        >
          拒绝
        </button>
      </div>
    </div>
  )
}
