import { useDialogueStore } from '../../stores/dialogueStore'
import { useOverlayStore } from '../../stores/overlayStore'
import type { DialogueEntry } from '../../types/game'

function LogMessage({ msg }: { msg: DialogueEntry }) {
  switch (msg.type) {
    case 'gm':
      return <p className="italic text-parchment-200 leading-relaxed py-0.5">{msg.content}</p>

    case 'gm_comment':
      return (
        <p className={
          msg.tone === 'introspective'
            ? 'font-serif italic text-parchment-200 leading-relaxed py-0.5 tracking-[0.01em]'
            : 'italic text-parchment-500 text-sm leading-relaxed py-0.5'
        }>
          {msg.content}
        </p>
      )

    case 'npc':
      return (
        <p className="py-0.5">
          <span className="text-sky-300 text-sm mr-2">{msg.speaker}</span>
          <span className="text-parchment-200">&ldquo;{msg.content}&rdquo;</span>
        </p>
      )

    case 'emote':
      return (
        <p className="py-0.5">
          <span className="text-purple-300/90 text-sm italic mr-1">{msg.speaker}</span>
          <span className="text-purple-300/90 italic text-sm">*{msg.content}*</span>
        </p>
      )

    case 'teammate':
      return (
        <p className="py-0.5 pl-3 text-sm">
          <span className="text-emerald-400 mr-1">{msg.speaker}</span>
          <span className="text-parchment-300">{msg.content}</span>
        </p>
      )

    case 'teammate_emote':
      return (
        <p className="py-0.5 pl-3">
          <span className="text-emerald-400 text-sm italic mr-1">{msg.speaker}</span>
          <span className="text-emerald-400/75 italic text-sm">*{msg.content}*</span>
        </p>
      )

    case 'system':
      return <p className="text-center text-parchment-500 text-xs py-1">── {msg.content} ──</p>

    case 'player':
      return <p className="text-right text-blue-300 text-sm py-0.5">你：{msg.content}</p>

    default:
      return <p className="text-parchment-400 text-sm py-0.5">{msg.content}</p>
  }
}

export default function LogOverlay() {
  const { log, messages } = useDialogueStore()
  const close = useOverlayStore((s) => s.close)
  const allMessages = [...log, ...messages]

  return (
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center">
      <div className="panel-ornate texture-noise w-full max-w-2xl max-h-[85vh] mx-4 flex flex-col">
        <div className="panel-header flex items-center justify-between flex-shrink-0">
          <h2 className="panel-title">对话记录</h2>
          <button
            onClick={close}
            className="text-parchment-500 hover:text-gold-400 transition-colors text-2xl leading-none"
          >
            ×
          </button>
        </div>
        <div className="panel-inset flex-1 overflow-y-auto m-4 px-4 py-3 space-y-0.5">
          {allMessages.length === 0 ? (
            <p className="text-parchment-500 text-center py-8">尚无记录</p>
          ) : (
            allMessages.map((msg) => <LogMessage key={msg.id} msg={msg} />)
          )}
        </div>
      </div>
    </div>
  )
}
