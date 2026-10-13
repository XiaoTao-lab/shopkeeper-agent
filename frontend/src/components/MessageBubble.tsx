/**
 * 聊天消息气泡组件
 * 组合展示用户问题、智能体回复、执行流程、一句话结论和结果表格，
 * 并在表格下方依次提供"自动图表"（柱状 / 折线）与可折叠的"可解释面板"。
 */
import { Bot, ChevronDown, Copy, Info, ShieldCheck, Sparkles, UserRound } from "lucide-react";
import { ResultChart } from "./ResultChart";
import { ResultTable } from "./ResultTable";
import { StepRail } from "./StepRail";
import { cn, formatTime, toClipboardText } from "../lib/format";
import { roleLabel } from "../lib/roles";
import type { ChatMessage, ExplainInfo } from "../types/agent";

/** 把耗时毫秒格式化成更易读的文案 */
function formatElapsed(ms?: number | null): string | null {
  if (ms === null || ms === undefined) return null;
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

/** 把时间区间压成一行可读文案，例如 "2025年第一季度（2025-01-01 ~ 2025-04-01）" */
function formatTimeRange(range: ExplainInfo["time_range"]): string | null {
  if (!range) return null;
  const span =
    range.start && range.end ? `${range.start} ~ ${range.end}` : null;
  if (range.expr && span) return `${range.expr}（${span}）`;
  return range.expr || span;
}

/** 可解释面板：把"这个数是怎么来的"摊开给用户看 */
function ExplainPanel({ explain }: { explain: ExplainInfo }) {
  const metrics = (explain.metrics ?? []).filter((metric) => metric.name || metric.formula);
  const timeRange = formatTimeRange(explain.time_range);
  const elapsed = formatElapsed(explain.elapsed_ms);
  const hasBody =
    Boolean(explain.rewritten_query) ||
    Boolean(timeRange) ||
    metrics.length > 0 ||
    Boolean(explain.sql) ||
    Boolean(elapsed);

  if (!hasBody) return null;

  return (
    <details className="group/explain mt-3 border border-ink/10 bg-white/45">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2 text-xs font-medium text-ink/60 transition hover:bg-ink/[0.03] [&::-webkit-details-marker]:hidden">
        <span className="inline-flex items-center gap-1.5">
          <Info className="h-3.5 w-3.5" aria-hidden="true" />
          这个数是怎么来的
        </span>
        <ChevronDown
          className="h-3.5 w-3.5 transition-transform group-open/explain:rotate-180"
          aria-hidden="true"
        />
      </summary>

      <div className="space-y-3 border-t border-ink/10 px-3 py-3 text-xs leading-5 text-ink/70">
        {explain.rewritten_query && (
          <div>
            <div className="mb-1 font-semibold uppercase tracking-[0.12em] text-ink/40">
              理解后的问题
            </div>
            <div className="text-ink/80">{explain.rewritten_query}</div>
          </div>
        )}

        {timeRange && (
          <div>
            <div className="mb-1 font-semibold uppercase tracking-[0.12em] text-ink/40">
              时间范围
            </div>
            <div className="text-ink/80">{timeRange}</div>
          </div>
        )}

        {metrics.length > 0 && (
          <div>
            <div className="mb-1 font-semibold uppercase tracking-[0.12em] text-ink/40">
              指标口径
            </div>
            <div className="space-y-1.5">
              {metrics.map((metric, index) => (
                <div
                  key={`${metric.name ?? "metric"}-${index}`}
                  className="border border-ink/10 bg-parchment/40 px-2.5 py-1.5"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-semibold text-ink/85">{metric.name}</span>
                    {metric.unit && (
                      <span className="text-ink/45">单位：{metric.unit}</span>
                    )}
                  </div>
                  {metric.formula && (
                    <code className="mt-0.5 block break-all font-mono text-[11px] text-moss">
                      {metric.formula}
                    </code>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {explain.sql && (
          <div>
            <div className="mb-1 font-semibold uppercase tracking-[0.12em] text-ink/40">
              执行的 SQL
            </div>
            <pre className="max-h-56 overflow-auto border border-ink/10 bg-ink/[0.04] px-2.5 py-2 font-mono text-[11px] leading-5 text-ink/80">
              {explain.sql}
            </pre>
          </div>
        )}

        {elapsed && (
          <div className="text-ink/45">耗时 {elapsed}</div>
        )}
      </div>
    </details>
  );
}

export function MessageBubble({
  message,
  onPickClarify,
}: {
  message: ChatMessage;
  onPickClarify?: (text: string) => void;
}) {
  const isUser = message.role === "user";

  const copy = async () => {
    const text = message.result ? toClipboardText(message.result) : message.content;
    await navigator.clipboard.writeText(text);
  };

  return (
    <article className={cn("group flex gap-3", isUser && "justify-end")}>
      {!isUser && (
        <div className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-ink text-parchment">
          <Bot className="h-4 w-4" aria-hidden="true" />
        </div>
      )}

      <div className={cn("max-w-[920px] flex-1", isUser && "flex max-w-[760px] justify-end")}>
        <div
          className={cn(
            "relative border px-5 py-4 shadow-line",
            isUser
              ? "border-ink/80 bg-ink text-parchment"
              : "border-ink/10 bg-[#fffaf1]/78 text-ink backdrop-blur",
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <p className="whitespace-pre-wrap text-[15px] leading-7">{message.content}</p>
            {!isUser && message.status !== "streaming" && (
              <button
                type="button"
                onClick={copy}
                className="shrink-0 rounded-full p-1.5 text-ink/45 opacity-0 outline-none transition hover:bg-ink/5 hover:text-ink focus:opacity-100 focus:ring-2 focus:ring-moss/40 group-hover:opacity-100"
                title="复制"
                aria-label="复制"
              >
                <Copy className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          </div>

          {message.error && (
            <div className="mt-3 border border-tomato/30 bg-tomato/10 px-3 py-2 text-sm text-tomato">
              {message.error}
            </div>
          )}

          {!isUser && message.clarify && message.clarify.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2">
              {message.clarify.map((option) => (
                <button
                  key={option.name}
                  type="button"
                  onClick={() => onPickClarify?.(option.name)}
                  title={option.description}
                  className="border border-moss/40 bg-moss/8 px-3 py-1.5 text-sm text-ink/85 transition hover:border-moss/70 hover:bg-moss/15"
                >
                  {option.name}
                </button>
              ))}
            </div>
          )}

          {!isUser && message.summary && (
            <div className="mt-3 flex gap-2.5 border border-moss/25 border-l-2 border-l-moss bg-moss/[0.06] px-3 py-2.5">
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-moss" aria-hidden="true" />
              <div>
                <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-moss">
                  结论
                </div>
                <p className="mt-0.5 text-sm leading-6 text-ink/85">{message.summary}</p>
              </div>
            </div>
          )}

          {!isUser && <StepRail steps={message.steps} />}

          {!isUser && message.permission && (
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
              <span className="inline-flex items-center gap-1.5 border border-ink/15 bg-ink/[0.03] px-2 py-1 text-ink/70">
                <ShieldCheck className="h-3.5 w-3.5 text-moss" aria-hidden="true" />
                角色：{roleLabel(message.permission.role)}
              </span>
              {message.permission.rls_applied && (
                <span className="inline-flex items-center border border-brass/40 bg-brass/10 px-2 py-1 text-brass">
                  已按行级权限过滤：仅显示授权区域
                </span>
              )}
              {message.permission.masked_columns && message.permission.masked_columns.length > 0 && (
                <span className="inline-flex items-center border border-ink/15 bg-ink/[0.03] px-2 py-1 text-ink/60">
                  已脱敏：{message.permission.masked_columns.join("、")}
                </span>
              )}
            </div>
          )}

          {!isUser && message.result !== undefined && <ResultTable data={message.result} />}

          {/* 自动图表：后端判定该出图时才下发 spec；放在表格下方，先看"数"再看"势" */}
          {!isUser && message.chart && message.result !== undefined && (
            <ResultChart spec={message.chart} data={message.result} />
          )}

          {!isUser && message.explain && <ExplainPanel explain={message.explain} />}

          <div
            className={cn(
              "mt-3 text-xs",
              isUser ? "text-parchment/55" : "text-ink/45",
            )}
          >
            {formatTime(message.createdAt)}
          </div>
        </div>
      </div>

      {isUser && (
        <div className="mt-1 grid h-9 w-9 shrink-0 place-items-center rounded-full bg-moss text-white">
          <UserRound className="h-4 w-4" aria-hidden="true" />
        </div>
      )}
    </article>
  );
}
