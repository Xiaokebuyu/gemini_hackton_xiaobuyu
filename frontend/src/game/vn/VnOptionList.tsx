import { useVnStore } from '../../stores/vnStore'
import { useOptionStore } from '../../stores/optionStore'
import { audio } from '../../lib/audio'
import type { InteractRequest } from '../../types/api'

function formatSkillTag(label: string): string | null {
  // Extract [技能 DC15] from label like "[说服 DC15] 我相信你能行"
  const match = label.match(/^\[([^\]]+)\]/)
  if (!match) return null
  return match[1]
}

function stripSkillTag(label: string): string {
  return label.replace(/^\[[^\]]+\]\s*/, '')
}

interface VnOptionListProps {
  sendInteract: (req: InteractRequest) => void
}

export default function VnOptionList({ sendInteract: _sendInteract }: VnOptionListProps) {
  const options = useOptionStore((s) => s.options)
  const isLocked = useOptionStore((s) => s.isLocked)
  const setShowingOptions = useVnStore((s) => s.setShowingOptions)
  const enqueueMessage = useVnStore((s) => s.enqueueMessage)

  const handleOptionClick = (opt: { id: string; label: string; action: () => void }) => {
    if (isLocked) return
    audio.playClick()
    // Add player message to VN queue so the dialogue history shows what was chosen
    const displayLabel = stripSkillTag(opt.label)
    enqueueMessage({ type: 'player', content: displayLabel })
    // Execute the option's action (fires SSE request via the closure from applyDialogueOptions)
    opt.action()
    // Hide options overlay
    setShowingOptions(false)
  }

  return (
    <>
      {options.map((opt) => {
        const skillTag = formatSkillTag(opt.label)
        const baseLabel = skillTag ? stripSkillTag(opt.label) : opt.label
        return (
          <button
            key={opt.id}
            onClick={() => handleOptionClick(opt)}
            disabled={opt.disabled ?? isLocked}
            className="
              bg-stone-900/60 hover:bg-stone-800/80
              border border-amber-500/20 hover:border-amber-400/40
              rounded-lg px-4 py-2.5
              transition-all duration-150
              disabled:opacity-50 disabled:cursor-not-allowed
              group
            "
          >
            <div className="flex items-start gap-2">
              {opt.icon && (
                <span className="text-base flex-shrink-0">{opt.icon}</span>
              )}
              <div className="flex-1 min-w-0 text-left">
                {skillTag && (
                  <span className="inline-block text-amber-400 text-xs mr-1.5 mb-0.5">
                    [{skillTag}]
                  </span>
                )}
                <span className="text-gray-100 text-sm leading-snug group-hover:text-white transition-colors">
                  {baseLabel}
                </span>
              </div>
            </div>
          </button>
        )
      })}
    </>
  )
}
