import React, { useMemo, useState } from 'react';
import { Brain, CheckCircle2, ChevronDown, ChevronUp, Wrench, XCircle } from 'lucide-react';
import { useGameStore } from '../../stores';

function compactJson(value: unknown, maxChars = 220): string {
  try {
    const text = JSON.stringify(value ?? {}, null, 0);
    if (!text) return '{}';
    if (text.length <= maxChars) return text;
    return `${text.slice(0, maxChars)}...`;
  } catch {
    return '{}';
  }
}

const AgenticTracePanel: React.FC = () => {
  const { latestAgenticTrace } = useGameStore();
  const [expanded, setExpanded] = useState(false);

  const normalized = useMemo(() => {
    if (!latestAgenticTrace || typeof latestAgenticTrace !== 'object') return null;
    const thinking = latestAgenticTrace.thinking ?? {};
    const toolCalls = Array.isArray(latestAgenticTrace.tool_calls)
      ? latestAgenticTrace.tool_calls
      : [];
    const stats = latestAgenticTrace.stats ?? {};
    return { thinking, toolCalls, stats };
  }, [latestAgenticTrace]);

  if (!normalized) return null;

  const { thinking, toolCalls, stats } = normalized;

  return (
    <section className="mt-3 rounded-xl border border-g-border bg-g-bg-surface-alt/80">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="w-full px-3 py-2 flex items-center justify-between text-left"
      >
        <div className="flex items-center gap-2 text-sm text-g-text-primary">
          <Brain className="w-4 h-4 text-g-gold" />
          <span className="font-semibold">GM Agentic Trace</span>
          <span className="text-xs text-g-text-muted">
            tools={typeof stats.count === 'number' ? stats.count : toolCalls.length}
          </span>
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 text-g-text-muted" /> : <ChevronDown className="w-4 h-4 text-g-text-muted" />}
      </button>

      {expanded && (
        <div className="px-3 pb-3 border-t border-g-border space-y-2">
          <div className="text-xs text-g-text-secondary leading-relaxed">
            {typeof thinking.summary === 'string' && thinking.summary.length > 0
              ? thinking.summary
              : '无思考摘要'}
          </div>

          <div className="flex flex-wrap gap-2 text-[11px] text-g-text-muted">
            <span className="px-2 py-0.5 rounded bg-g-bg-surface border border-g-border">
              thoughts: {thinking.thoughts_token_count ?? 0}
            </span>
            <span className="px-2 py-0.5 rounded bg-g-bg-surface border border-g-border">
              output: {thinking.output_token_count ?? 0}
            </span>
            <span className="px-2 py-0.5 rounded bg-g-bg-surface border border-g-border">
              total: {thinking.total_token_count ?? 0}
            </span>
            {thinking.finish_reason ? (
              <span className="px-2 py-0.5 rounded bg-g-bg-surface border border-g-border">
                finish: {thinking.finish_reason}
              </span>
            ) : null}
          </div>

          <div className="space-y-2">
            {toolCalls.length === 0 ? (
              <div className="text-xs text-g-text-muted">本轮无工具调用。</div>
            ) : (
              toolCalls.map((call, index) => {
                const ok = call?.success !== false;
                return (
                  <div
                    key={`${call?.index ?? index}-${call?.name ?? 'tool'}`}
                    className="rounded-lg border border-g-border px-2 py-1.5 bg-g-bg-surface"
                  >
                    <div className="flex items-center gap-2 text-xs">
                      <Wrench className="w-3.5 h-3.5 text-g-gold" />
                      <span className="font-medium text-g-text-primary">
                        {call?.name || 'unknown_tool'}
                      </span>
                      <span className="text-g-text-muted">
                        #{typeof call?.index === 'number' ? call.index : index + 1}
                      </span>
                      <span className="text-g-text-muted">
                        {typeof call?.duration_ms === 'number' ? `${call.duration_ms}ms` : '--'}
                      </span>
                      {ok ? (
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
                      ) : (
                        <XCircle className="w-3.5 h-3.5 text-rose-500" />
                      )}
                    </div>
                    <div className="mt-1 text-[11px] text-g-text-muted break-all">
                      args: <code>{compactJson(call?.args)}</code>
                    </div>
                    {!ok && typeof call?.error === 'string' && call.error ? (
                      <div className="mt-0.5 text-[11px] text-rose-400 break-all">
                        error: {call.error}
                      </div>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </section>
  );
};

export default AgenticTracePanel;
