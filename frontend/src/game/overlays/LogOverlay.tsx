import { useDialogueStore } from '../../stores/dialogueStore'
import { useOverlayStore } from '../../stores/overlayStore'
import type { DialogueEntry } from '../../types/game'

function LogMessage({ msg }: { msg: DialogueEntry }) {
  switch (msg.type) {
    case 'gm':
      return <p className="italic text-amber-200/80 leading-relaxed py-0.5">{msg.content}</p>

    case 'npc':
      return (
        <p className="py-0.5">
          <span className="text-sky-400 text-sm mr-2">{msg.speaker}</span>
          <span className="text-gray-200">&ldquo;{msg.content}&rdquo;</span>
        </p>
      )

    case 'emote':
      return (
        <p className="py-0.5">
          <span className="text-purple-300 text-sm italic mr-1">{msg.speaker}</span>
          <span className="text-purple-200/70 italic text-sm">*{msg.content}*</span>
        </p>
      )

    case 'teammate':
      return (
        <p className="py-0.5 pl-3 text-sm">
          <span className="text-green-400 mr-1">{msg.speaker}</span>
          <span className="text-gray-300">{msg.content}</span>
        </p>
      )

    case 'system':
      return <p className="text-center text-gray-500 text-xs py-1">── {msg.content} ──</p>

    case 'player':
      return <p className="text-right text-blue-300 text-sm py-0.5">你：{msg.content}</p>

    default:
      return <p className="text-gray-400 text-sm py-0.5">{msg.content}</p>
  }
}

export default function LogOverlay() {
  const { log, messages } = useDialogueStore()
  const close = useOverlayStore((s) => s.close)
  const allMessages = [...log, ...messages]

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/95 flex flex-col">
      <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800 flex-shrink-0">
        <h2 className="text-amber-400 font-bold">对话记录</h2>
        <button
          onClick={close}
          className="text-gray-400 hover:text-gray-200 text-2xl leading-none"
        >
          ×
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-0.5">
        {allMessages.length === 0 ? (
          <p className="text-gray-600 text-center py-8">尚无记录</p>
        ) : (
          allMessages.map((msg) => <LogMessage key={msg.id} msg={msg} />)
        )}
      </div>
    </div>
  )
}
