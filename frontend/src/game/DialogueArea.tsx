import { useSceneStore } from '../stores/sceneStore'
import DialogueHistory from './DialogueHistory'
import OptionPanel from './OptionPanel'
import QuickBar from './QuickBar'
import type { InteractRequest } from '../types/api'
import type { OverviewHandlers } from '../stores/optionStore'

interface Props {
  worldId: string
  sessionId: string
  sendInteract: (req: InteractRequest) => void
  overviewHandlers: OverviewHandlers
}

export default function DialogueArea({ worldId, sessionId, sendInteract, overviewHandlers }: Props) {
  const gameMode = useSceneStore((s) => s.gameMode)
  const openingInProgress = useSceneStore((s) => s.openingInProgress)
  const privateChatBorder = gameMode === 'private_chat' ? 'border-t-2 border-purple-500/60' : ''

  return (
    <div
      className={`absolute bottom-0 left-0 right-0 z-10 flex flex-col bg-black/75 backdrop-blur-sm ${privateChatBorder}`}
      style={{ height: 'min(38vh, calc(100vh - 80px))', minHeight: 'clamp(160px, 28vh, 280px)' }}
    >
      <div className="flex flex-col h-full px-3 pt-2 pb-2 gap-1 min-h-0">
        <DialogueHistory />
        <OptionPanel sendInteract={sendInteract} overviewHandlers={overviewHandlers} />
        {!openingInProgress && <QuickBar worldId={worldId} sessionId={sessionId} />}
      </div>
    </div>
  )
}
