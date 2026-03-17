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
    <div className="fixed inset-0 z-20 bg-black/80 backdrop-blur-[2px] flex items-center justify-center px-4">
      <div className="panel-ornate texture-noise w-full max-w-sm">
        <div className="panel-header flex items-center justify-between">
          <div>
            <h2 className="panel-title">奉献</h2>
            <p className="text-xs text-parchment-500 mt-0.5">{sourceName}</p>
          </div>
          <button
            onClick={overlay.close}
            className="text-parchment-500 hover:text-gold-400 transition-colors text-lg leading-none"
          >
            ×
          </button>
        </div>

        <div className="space-y-4 px-5 py-5">
          <p className="text-parchment-300 text-sm leading-relaxed">
            选择要投入的金额。当前持有 <span className="text-gold-400 font-mono">{gold}G</span>
          </p>

          <div className="grid grid-cols-3 gap-3">
            {DONATION_AMOUNTS.map((amount) => {
              const disabled = gold < amount
              return (
                <button
                  key={amount}
                  onClick={() => donate(amount)}
                  disabled={disabled}
                  className={disabled ? 'btn-subtle opacity-40 cursor-not-allowed py-4' : 'btn-fantasy py-4'}
                >
                  {amount}G
                </button>
              )
            })}
          </div>

          <p className="text-xs text-parchment-500">
            金币不足的选项不可选择。
          </p>
        </div>
      </div>
    </div>
  )
}
