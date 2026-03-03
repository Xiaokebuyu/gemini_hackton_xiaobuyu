import { useState } from 'react'
import { useOptionStore } from '../stores/optionStore'
import type { TextInputRequest } from '../types/api'

interface Props {
  sendInput: (req: TextInputRequest) => void
}

export default function OptionPanel({ sendInput }: Props) {
  const [text, setText] = useState('')
  const { options, isLocked } = useOptionStore()

  const handleSend = () => {
    const trimmed = text.trim()
    if (!trimmed || isLocked) return
    sendInput({ text: trimmed })
    setText('')
  }

  return (
    <div className="flex-shrink-0 border-t border-gray-700/50 pt-2">
      {/* 选项按钮列表 */}
      {isLocked ? (
        <div className="text-gray-500 text-sm py-1 px-1">思考中...</div>
      ) : options.length > 0 ? (
        <div className="flex flex-wrap gap-1.5 mb-2 max-h-28 overflow-y-auto">
          {options.map((opt) => (
            <button
              key={opt.id}
              onClick={() => opt.action()}
              disabled={opt.disabled ?? false}
              className="text-left px-3 py-1.5 rounded-lg bg-gray-800/80 hover:bg-gray-700 border border-gray-600/50 hover:border-gray-500 text-gray-200 text-sm transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {opt.icon && <span className="mr-1.5">{opt.icon}</span>}
              {opt.label}
            </button>
          ))}
        </div>
      ) : null}

      {/* 自由输入框 */}
      <div className="flex gap-2">
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSend()}
          disabled={isLocked}
          placeholder={isLocked ? '请等待...' : '输入你想做的事...'}
          className="flex-1 bg-gray-800/60 border border-gray-600/50 text-gray-100 placeholder-gray-500 rounded-lg px-3 py-1.5 text-sm focus:border-amber-500/70 outline-none disabled:opacity-50"
        />
        <button
          onClick={handleSend}
          disabled={isLocked || !text.trim()}
          className="bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white px-4 py-1.5 rounded-lg text-sm transition-colors"
        >
          发送
        </button>
      </div>
    </div>
  )
}
