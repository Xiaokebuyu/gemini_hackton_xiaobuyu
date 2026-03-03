import DialogueHistory from './DialogueHistory'
import OptionPanel from './OptionPanel'
import QuickBar from './QuickBar'
import type { TextInputRequest } from '../types/api'

interface Props {
  worldId: string
  sessionId: string
  sendInput: (req: TextInputRequest) => void
}

export default function DialogueArea({ worldId, sessionId, sendInput }: Props) {
  return (
    <div
      className="absolute bottom-0 left-0 right-0 z-10 flex flex-col bg-black/75 backdrop-blur-sm"
      style={{ height: 'min(38vh, calc(100vh - 80px))', minHeight: '280px' }}
    >
      <div className="flex flex-col h-full px-3 pt-2 pb-2 gap-1 min-h-0">
        <DialogueHistory />
        <OptionPanel sendInput={sendInput} />
        <QuickBar worldId={worldId} sessionId={sessionId} />
      </div>
    </div>
  )
}
