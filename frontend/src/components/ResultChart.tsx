/**
 * 结果图表组件（零依赖手写 SVG）
 *
 * 后端用纯规则给出"画图说明书"（ChartSpec），这里只做"哑渲染"：
 * 按 spec.type 画柱状或折线，数据仍来自 result 事件，不重复传输。
 *
 * 刻意不引入图表库：
 * - 本场景只需要柱状 / 折线两种图，且数据是聚合后的小结果集；
 * - 手写 SVG 能与整站主题色（parchment / ink / moss / brass / tomato）完全一致，
 *   不必再跟图表库的默认样式打架；
 * - 少一个依赖、少一份版本风险。
 */
import { Activity, BarChart3 } from "lucide-react";
import type { ChartSpec } from "../types/agent";

/** 多系列配色，取自整站主题色（moss / brass / tomato） */
const SERIES_COLORS = ["#2f6b4f", "#b48638", "#d64e3a"];

/** SVG 逻辑画布尺寸（实际显示宽度由容器决定，等比缩放） */
const W = 720;
const H = 300;
const PAD = { left: 68, right: 20, top: 24, bottom: 56 };
/** x 轴最多显示多少个刻度标签（多了就抽稀，避免互相压字） */
const MAX_X_LABELS = 12;
/** 折线超过这个点数就不再画圆点（否则点连成一片） */
const MAX_DOTS = 24;

type Point = { label: string; values: number[] };

function normalizeRows(data: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(data)) return [];
  return data.filter(
    (item): item is Record<string, unknown> =>
      Boolean(item) && typeof item === "object" && !Array.isArray(item),
  );
}

/** 把数值压成适合坐标轴的短标签（12000 → 1.2万） */
function compact(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 100000000) return `${(value / 100000000).toFixed(1)}亿`;
  if (abs >= 10000) return `${(value / 10000).toFixed(1)}万`;
  if (Number.isInteger(value)) return value.toLocaleString("zh-CN");
  return value.toFixed(2);
}

/** 把最大值向上取到"好看"的整数，让刻度落在整数上（107373 → 200000） */
function niceCeil(value: number): number {
  if (value <= 0) return 1;
  const exponent = Math.floor(Math.log10(value));
  const base = 10 ** exponent;
  const normalized = value / base;
  const step = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10;
  return step * base;
}

/** x 轴文字过长时截断，避免相邻标签挤压 */
function clip(label: string, max = 6): string {
  return label.length > max ? `${label.slice(0, max)}…` : label;
}

export function ResultChart({ spec, data }: { spec: ChartSpec; data: unknown }) {
  const dimension = spec.dimension;
  const measures = (spec.measures ?? []).slice(0, SERIES_COLORS.length);
  const keys = spec.order ?? [];

  // 维度取值 → 结果行；再按后端给的轴序还原成绘图顺序
  const byKey = new Map<string, Record<string, unknown>>();
  for (const row of normalizeRows(data)) {
    const key = String(row[dimension] ?? "");
    if (key && !byKey.has(key)) byKey.set(key, row);
  }

  const points: Point[] = keys
    .map((key) => {
      const row = byKey.get(key);
      if (!row) return null;
      return {
        label: key,
        values: measures.map((measure) => {
          const value = Number(row[measure]);
          return Number.isFinite(value) ? value : 0;
        }),
      };
    })
    .filter((point): point is Point => point !== null);

  // 数据不足两个类目就没有"对比 / 趋势"可言，直接不渲染
  if (!dimension || measures.length === 0 || points.length < 2) return null;

  const isLine = spec.type === "line";
  const unit = spec.unit ?? "";

  // —— 纵轴范围：柱状与折线都包含 0，有负值则向下延伸 ——
  const allValues = points.flatMap((point) => point.values);
  const yMax = niceCeil(Math.max(0, ...allValues) || 1);
  const rawMin = Math.min(0, ...allValues);
  const yMin = rawMin < 0 ? -niceCeil(-rawMin) : 0;

  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const span = yMax - yMin || 1;
  const yOf = (value: number) => PAD.top + ((yMax - value) / span) * plotH;
  const zeroY = yOf(0);

  const band = plotW / points.length;
  const groupW = band * 0.68;
  const gap = measures.length > 1 ? 2 : 0;
  const barW = Math.max(2, (groupW - gap * (measures.length - 1)) / measures.length);
  const labelStep = Math.ceil(points.length / MAX_X_LABELS);

  const ticks = Array.from({ length: 5 }, (_, index) => yMin + (span * index) / 4);

  return (
    <section className="mt-3 overflow-hidden border border-ink/10 bg-white/70 shadow-line">
      <div className="flex items-center justify-between gap-3 border-b border-ink/10 px-4 py-3">
        <div className="flex min-w-0 items-center gap-2 text-sm font-semibold text-ink">
          {isLine ? (
            <Activity className="h-4 w-4 shrink-0 text-moss" aria-hidden="true" />
          ) : (
            <BarChart3 className="h-4 w-4 shrink-0 text-moss" aria-hidden="true" />
          )}
          <span className="shrink-0">{isLine ? "趋势" : "对比"}</span>
          <span className="truncate font-normal text-ink/50">
            {dimension} · {measures.join(" / ")}
            {unit ? `（${unit}）` : ""}
          </span>
        </div>
        {spec.reason && (
          <div className="hidden shrink-0 text-xs text-ink/45 sm:block" title={spec.reason}>
            {spec.reason}
          </div>
        )}
      </div>

      {measures.length > 1 && (
        <div className="flex flex-wrap items-center gap-3 border-b border-ink/10 px-4 py-2 text-xs text-ink/65">
          {measures.map((measure, index) => (
            <span key={measure} className="inline-flex items-center gap-1.5">
              <span
                className="h-2.5 w-2.5"
                style={{ backgroundColor: SERIES_COLORS[index] }}
                aria-hidden="true"
              />
              {measure}
            </span>
          ))}
        </div>
      )}

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="block h-auto w-full"
        role="img"
        aria-label={`${dimension}各${measures.join("、")}的${isLine ? "趋势" : "对比"}图`}
      >
        {/* 横向网格线与纵轴刻度 */}
        {ticks.map((value) => (
          <g key={value}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={yOf(value)}
              y2={yOf(value)}
              stroke="#20201d"
              strokeOpacity={0.08}
              strokeWidth={1}
            />
            <text
              x={PAD.left - 10}
              y={yOf(value)}
              textAnchor="end"
              dominantBaseline="central"
              fontSize={12}
              fill="#20201d"
              fillOpacity={0.5}
            >
              {compact(value)}
            </text>
          </g>
        ))}

        {/* 有负值时把零轴画深一点，避免误读柱子的起算点 */}
        {yMin < 0 && (
          <line
            x1={PAD.left}
            x2={W - PAD.right}
            y1={zeroY}
            y2={zeroY}
            stroke="#20201d"
            strokeOpacity={0.3}
            strokeWidth={1}
          />
        )}

        {/* 柱状：每个类目一组，组内按度量分色 */}
        {!isLine &&
          points.map((point, index) => (
            <g key={point.label}>
              {point.values.map((value, series) => {
                const x =
                  PAD.left +
                  band * index +
                  (band - groupW) / 2 +
                  series * (barW + gap);
                const top = value >= 0 ? yOf(value) : zeroY;
                const height = Math.max(1, Math.abs(zeroY - yOf(value)));
                return (
                  <rect
                    key={series}
                    x={x}
                    y={top}
                    width={barW}
                    height={height}
                    fill={SERIES_COLORS[series % SERIES_COLORS.length]}
                    fillOpacity={0.85}
                  >
                    <title>
                      {`${point.label} · ${measures[series]} ${compact(value)}${unit}`}
                    </title>
                  </rect>
                );
              })}
            </g>
          ))}

        {/* 折线：单度量，点太多时省略圆点 */}
        {isLine && (
          <g>
            <polyline
              points={points
                .map((point, index) => `${PAD.left + band * (index + 0.5)},${yOf(point.values[0])}`)
                .join(" ")}
              fill="none"
              stroke={SERIES_COLORS[0]}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {points.length <= MAX_DOTS &&
              points.map((point, index) => (
                <circle
                  key={point.label}
                  cx={PAD.left + band * (index + 0.5)}
                  cy={yOf(point.values[0])}
                  r={3.5}
                  fill={SERIES_COLORS[0]}
                  stroke="#fffaf1"
                  strokeWidth={1.5}
                >
                  <title>
                    {`${point.label} · ${measures[0]} ${compact(point.values[0])}${unit}`}
                  </title>
                </circle>
              ))}
          </g>
        )}

        {/* 横轴与其刻度标签（超量则抽稀） */}
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={H - PAD.bottom}
          y2={H - PAD.bottom}
          stroke="#20201d"
          strokeOpacity={0.2}
          strokeWidth={1}
        />
        {points.map((point, index) =>
          index % labelStep === 0 ? (
            <text
              key={point.label}
              x={PAD.left + band * (index + 0.5)}
              y={H - PAD.bottom + 22}
              textAnchor="middle"
              fontSize={12}
              fill="#20201d"
              fillOpacity={0.6}
            >
              {clip(point.label)}
            </text>
          ) : null,
        )}
      </svg>
    </section>
  );
}
