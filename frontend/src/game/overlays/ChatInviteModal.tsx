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
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center">
      <div className="panel-ornate texture-noise w-72">
        <div className="panel-header">
          <h2 className="panel-title text-center">私语邀请</h2>
        </div>
        <div className="p-4">
          <p className="text-parchment-300 text-sm text-center mb-4">
            <span className="font-display text-gold-300">
              {invite?.npc_name ?? invite?.npc_id ?? '某人'}
            </span>{' '}
            想和你说些悄悄话...
          </p>
          <div className="flex gap-2">
            <button
              onClick={handleAccept}
              className="btn-fantasy flex-1 py-1.5"
            >
              接受
            </button>
            <button
              onClick={close}
              className="btn-subtle flex-1 py-1.5"
            >
              拒绝
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
