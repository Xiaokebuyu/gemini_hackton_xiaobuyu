import { useOverlayStore } from '../../stores/overlayStore'
import { usePlayerStore } from '../../stores/playerStore'
import type { StructuredActionRequest } from '../../types/api'
import type { DonationTarget } from '../../types/game'

const DONATION_AMOUNTS = [5, 10, 20]

interface Props {
  sendAction: (req: StructuredActionRequest) => Promise<void> | void
}

export default function DonationOverlay({ sendAction }: Props) {
  const overlay = useOverlayStore()
  const gold = usePlayerStore((state) => state.gold)
  const data = overlay.data as DonationTarget | null

  if (!data?.targetId) return null

  const sourceName = data.sourceName?.trim() || data.targetId

  const donate = (amount: number) => {
    overlay.close()
    void sendAction({
      action_type: 'donate',
      params: {
        target_kind: data.targetKind,
        target_id: data.targetId,
        amount,
      },
    })
  }

  return (
    <div className="fixed inset-0 z-20 bg-gray-950/90 flex items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-xl border border-amber-700/40 bg-gray-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-gray-700 px-5 py-3">
          <div>
            <h2 className="text-sm font-bold text-amber-300">奉献</h2>
            <p className="text-xs text-gray-500">{sourceName}</p>
          </div>
          <button
            onClick={overlay.close}
            className="text-lg leading-none text-gray-500 hover:text-gray-300"
          >
            ×
          </button>
        </div>

        <div className="space-y-4 px-5 py-5">
          <p className="text-sm leading-relaxed text-gray-300">
            选择要投入的金额。当前持有 <span className="text-amber-300">{gold}G</span>
          </p>

          <div className="grid grid-cols-3 gap-3">
            {DONATION_AMOUNTS.map((amount) => {
              const disabled = gold < amount
              return (
                <button
                  key={amount}
                  onClick={() => donate(amount)}
                  disabled={disabled}
                  className={[
                    'rounded-lg border px-3 py-4 text-sm transition',
                    disabled
                      ? 'cursor-not-allowed border-gray-800 bg-gray-900 text-gray-600'
                      : 'border-amber-700/50 bg-amber-950/30 text-amber-200 hover:border-amber-500 hover:bg-amber-900/40',
                  ].join(' ')}
                >
                  {amount}G
                </button>
              )
            })}
          </div>

          <p className="text-xs text-gray-500">
            金币不足的选项不可选择。
          </p>
        </div>
      </div>
    </div>
  )
}
