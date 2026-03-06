import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getCharacterOptions, createCharacter } from '../lib/api'
import { usePlayerStore } from '../stores/playerStore'
import { useSessionStore } from '../stores/sessionStore'
import type { CharacterCreationOptions, CharacterOption } from '../types/api'

// ─── 属性配置 ────────────────────────────────────────────────────────────────

const ATTRS = ['STR', 'DEX', 'CON', 'INT', 'WIS', 'CHA'] as const
type Attr = (typeof ATTRS)[number]

const ATTR_LABELS: Record<Attr, string> = {
  STR: '力量',
  DEX: '敏捷',
  CON: '体质',
  INT: '智力',
  WIS: '感知',
  CHA: '魅力',
}

const ATTR_API_KEYS: Record<Attr, string> = {
  STR: 'str',
  DEX: 'dex',
  CON: 'con',
  INT: 'int',
  WIS: 'wis',
  CHA: 'cha',
}

const STANDARD_ARRAY = [15, 14, 13, 12, 10, 8]

// ─── 选项列表（模块级，无 hook，可安全作为 JSX 组件） ────────────────────────

interface SelectionStepProps {
  label: string
  items: CharacterOption[]
  selected: string
  onSelect: (id: string) => void
}

function SelectionStep({ label, items, selected, onSelect }: SelectionStepProps) {
  return (
    <div>
      <h2 className="text-xl font-bold text-amber-400 mb-4">选择{label}</h2>
      <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-1">
        {items.map((item) => (
          <button
            key={item.id}
            onClick={() => onSelect(item.id)}
            className={`w-full text-left p-4 rounded-lg border transition-colors ${
              selected === item.id
                ? 'border-amber-500 bg-amber-900/30'
                : 'border-gray-600 bg-gray-800 hover:border-gray-400'
            }`}
          >
            <div className="font-bold text-gray-100 mb-1">{item.name}</div>
            {item.description && (
              <div className="text-gray-400 text-sm">{item.description}</div>
            )}
          </button>
        ))}
      </div>
    </div>
  )
}

// ─── 主页面 ──────────────────────────────────────────────────────────────────

interface FormState {
  race: string
  characterClass: string
  background: string
  abilityScores: Partial<Record<Attr, number>>
  name: string
  backstory: string
}

const EMPTY_FORM: FormState = {
  race: '',
  characterClass: '',
  background: '',
  abilityScores: {},
  name: '',
  backstory: '',
}

export default function CharacterCreatePage() {
  const { worldId, sid } = useParams<{ worldId: string; sid: string }>()
  const navigate = useNavigate()
  const setSession = useSessionStore((s) => s.setSession)
  const updateFromPanel = usePlayerStore((s) => s.updateFromPanel)

  const [step, setStep] = useState(1)
  const [options, setOptions] = useState<CharacterCreationOptions | null>(null)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!worldId) return
    getCharacterOptions(worldId)
      .then(setOptions)
      .catch((err: Error) => setError(err.message ?? '加载选项失败'))
  }, [worldId])

  // ── 步骤可通过判断 ──────────────────────────────────────────────────────────

  const canProceed = (): boolean => {
    if (step === 1) return !!form.race
    if (step === 2) return !!form.characterClass
    if (step === 3) return !!form.background
    if (step === 4) return ATTRS.every((a) => form.abilityScores[a] != null)
    if (step === 5) return form.name.trim().length > 0
    return false
  }

  // ── 提交 ────────────────────────────────────────────────────────────────────

  const handleSubmit = async () => {
    if (!worldId || !sid) return
    setSubmitting(true)
    setError(null)
    try {
      const abilityScores: Record<string, number> = {}
      for (const attr of ATTRS) {
        abilityScores[ATTR_API_KEYS[attr]] = form.abilityScores[attr] ?? 8
      }
      const result = await createCharacter(worldId, sid, {
        name: form.name.trim(),
        race: form.race,
        character_class: form.characterClass,
        background: form.background,
        ability_scores: abilityScores,
        backstory: form.backstory.trim() || undefined,
      })
      setSession(worldId, sid, result.phase)
      updateFromPanel(result)
      navigate(`/${worldId}/sessions/${sid}/play`)
    } catch (err: unknown) {
      setError((err as Error).message ?? '创建失败')
    } finally {
      setSubmitting(false)
    }
  }

  // ── 加载态 ──────────────────────────────────────────────────────────────────

  if (!options) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center text-gray-400">
        {error ?? '加载中...'}
      </div>
    )
  }

  // ── 属性分配步骤内容 ────────────────────────────────────────────────────────

  const usedValues = ATTRS.map((a) => form.abilityScores[a]).filter((v): v is number => v != null)

  const abilityStepContent = (
    <div>
      <h2 className="text-xl font-bold text-amber-400 mb-2">分配属性</h2>
      <p className="text-gray-400 text-sm mb-4">
        标准数组：[15, 14, 13, 12, 10, 8]，每个数值只能使用一次。
      </p>
      <div className="space-y-3">
        {ATTRS.map((attr) => {
          const currentVal = form.abilityScores[attr]
          const available = STANDARD_ARRAY.filter(
            (v) => v === currentVal || !usedValues.includes(v),
          ).sort((a, b) => b - a)
          return (
            <div key={attr} className="flex items-center gap-4 bg-gray-800 p-3 rounded-lg">
              <span className="w-24 text-amber-300 font-bold">
                {attr}{' '}
                <span className="text-gray-500 text-xs font-normal">{ATTR_LABELS[attr]}</span>
              </span>
              <select
                value={currentVal ?? ''}
                onChange={(e) => {
                  const val = Number(e.target.value)
                  setForm((f) => ({
                    ...f,
                    abilityScores: { ...f.abilityScores, [attr]: val },
                  }))
                }}
                className="flex-1 bg-gray-700 text-gray-100 border border-gray-600 rounded px-3 py-1.5 focus:border-amber-500 outline-none"
              >
                <option value="">— 未分配 —</option>
                {available.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </select>
              {currentVal != null && (
                <span className="text-amber-400 font-bold w-6 text-center">{currentVal}</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )

  // ── 命名 + 确认步骤内容 ─────────────────────────────────────────────────────

  const raceName = options.races.find((r) => r.id === form.race)?.name ?? form.race
  const className = options.classes.find((c) => c.id === form.characterClass)?.name ?? form.characterClass
  const bgName = options.backgrounds.find((b) => b.id === form.background)?.name ?? form.background

  const summaryStepContent = (
    <div>
      <h2 className="text-xl font-bold text-amber-400 mb-4">角色命名与确认</h2>
      <div className="space-y-4">
        <div>
          <label className="block text-gray-300 text-sm mb-1">
            角色名 <span className="text-red-400">*</span>
          </label>
          <input
            type="text"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder="输入角色名..."
            className="w-full bg-gray-800 border border-gray-600 text-gray-100 rounded-lg px-4 py-2 focus:border-amber-500 outline-none"
          />
        </div>
        <div>
          <label className="block text-gray-300 text-sm mb-1">背景故事（可选）</label>
          <textarea
            value={form.backstory}
            onChange={(e) => setForm((f) => ({ ...f, backstory: e.target.value }))}
            placeholder="你的角色从何处来..."
            rows={3}
            className="w-full bg-gray-800 border border-gray-600 text-gray-100 rounded-lg px-4 py-2 focus:border-amber-500 outline-none resize-none"
          />
        </div>
        <div className="bg-gray-800 rounded-lg p-4 text-sm">
          <div className="text-gray-400 mb-2 font-bold">角色摘要</div>
          <div className="space-y-1 text-gray-300">
            <div>
              种族：<span className="text-amber-300">{raceName}</span>
            </div>
            <div>
              职业：<span className="text-amber-300">{className}</span>
            </div>
            <div>
              背景：<span className="text-amber-300">{bgName}</span>
            </div>
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
              {ATTRS.map((a) => (
                <span key={a}>
                  {a}{' '}
                  <span className="text-amber-300 font-bold">
                    {form.abilityScores[a] ?? '?'}
                  </span>
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )

  // ── 渲染 ────────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-8">
      <div className="max-w-2xl mx-auto">
        {/* 步骤条 */}
        <div className="flex items-center justify-center gap-3 mb-8">
          {[1, 2, 3, 4, 5].map((i) => (
            <div
              key={i}
              className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold transition-colors ${
                i === step
                  ? 'bg-amber-600 text-white'
                  : i < step
                    ? 'bg-amber-900 text-amber-400'
                    : 'bg-gray-700 text-gray-500'
              }`}
            >
              {i}
            </div>
          ))}
        </div>

        {/* 步骤内容 */}
        <div className="mb-6">
          {step === 1 && (
            <SelectionStep
              label="种族"
              items={options.races}
              selected={form.race}
              onSelect={(id) => setForm((f) => ({ ...f, race: id }))}
            />
          )}
          {step === 2 && (
            <SelectionStep
              label="职业"
              items={options.classes}
              selected={form.characterClass}
              onSelect={(id) => setForm((f) => ({ ...f, characterClass: id }))}
            />
          )}
          {step === 3 && (
            <SelectionStep
              label="背景"
              items={options.backgrounds}
              selected={form.background}
              onSelect={(id) => setForm((f) => ({ ...f, background: id }))}
            />
          )}
          {step === 4 && abilityStepContent}
          {step === 5 && summaryStepContent}
        </div>

        {error && <div className="text-red-400 text-sm mb-4">{error}</div>}

        {/* 导航按钮 */}
        <div className="flex justify-between">
          {step > 1 ? (
            <button
              onClick={() => setStep((s) => s - 1)}
              className="bg-gray-700 hover:bg-gray-600 text-gray-200 px-6 py-2 rounded-lg transition-colors"
            >
              上一步
            </button>
          ) : (
            <button
              onClick={() => navigate(-1)}
              className="bg-gray-700 hover:bg-gray-600 text-gray-200 px-6 py-2 rounded-lg transition-colors"
            >
              返回
            </button>
          )}

          {step < 5 ? (
            <button
              onClick={() => setStep((s) => s + 1)}
              disabled={!canProceed()}
              className="bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white px-6 py-2 rounded-lg transition-colors"
            >
              下一步
            </button>
          ) : (
            <button
              onClick={handleSubmit}
              disabled={!canProceed() || submitting}
              className="bg-amber-600 hover:bg-amber-500 disabled:opacity-40 text-white px-6 py-2 rounded-lg transition-colors"
            >
              {submitting ? '创建中...' : '创建角色'}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
