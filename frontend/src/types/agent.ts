/**
 * 智能体类型定义
 * 定义问数智能体前端使用的 SSE 事件、流程步骤和聊天消息类型
 */
export type ProgressStatus = "running" | "success" | "error";

export type ProgressEvent = {
  type: "progress";
  step: string;
  status: ProgressStatus;
};

// 可解释信息中命中的单个指标口径
export type ExplainMetric = {
  name?: string;
  formula?: string;
  base_table?: string;
  unit?: string;
};

// 解析出的时间区间（半开区间 [start, end)）
export type ExplainTimeRange = {
  start?: string;
  end?: string;
  granularity?: string;
  expr?: string;
};

// 可解释信息：系统把问题理解成了什么、用了哪个口径、执行了什么 SQL、耗时多久
export type ExplainInfo = {
  rewritten_query?: string;
  metrics?: ExplainMetric[];
  sql?: string;
  time_range?: ExplainTimeRange | null;
  elapsed_ms?: number | null;
};

// 权限提示：本轮以什么角色执行、是否做了行级过滤 / 列脱敏
export type PermissionInfo = {
  role?: string;
  rls_applied?: boolean;
  region_ids?: string[];
  masked_columns?: string[];
};

// 图表说明书：后端用纯规则判断"该画什么图"，前端照着渲染
// 只描述怎么画（维度 / 度量 / 轴序），不下发数据——数据仍走 result
export type ChartSpec = {
  type: "bar" | "line";
  dimension: string;
  measures: string[];
  unit?: string | null;
  order: string[];
  reason?: string;
};

export type ResultEvent = {
  type: "result";
  data: unknown;
  // 空结果标记：当后端判定为"无数据"时为 true，并附带一句可读提示
  empty?: boolean;
  message?: string;
  // 一句话结论：把结果表压成一句人话（仅在有数据时下发）
  summary?: string;
  // 图表说明书：时间→折线、对比→柱状；判定不该出图时不下发
  chart?: ChartSpec;
  // 可解释信息：问题理解 / 指标口径 / SQL / 耗时
  explain?: ExplainInfo;
  // 权限提示：角色、是否行级过滤、脱敏列
  permission?: PermissionInfo;
};

export type ErrorEvent = {
  type: "error";
  message: string;
};

// 会话元信息：后端在流开始时下发本次会话 id，用于多轮续接
export type MetaEvent = {
  type: "meta";
  thread_id: string;
};

// 澄清追问：问题有指标歧义时，后端反问并给出候选指标供用户选择
export type ClarifyOption = {
  name: string;
  description?: string;
};

export type ClarifyEvent = {
  type: "clarify";
  message: string;
  options?: ClarifyOption[];
};

export type AgentEvent =
  | ProgressEvent
  | ResultEvent
  | ErrorEvent
  | MetaEvent
  | ClarifyEvent;

export type StepState = {
  step: string;
  status: ProgressStatus;
  updatedAt: number;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "streaming" | "done" | "error";
  steps?: StepState[];
  result?: unknown;
  error?: string;
  // 澄清追问的候选指标：渲染成可点击的选项，点击后把指标名填入输入框
  clarify?: ClarifyOption[];
  // 一句话结论：渲染在结果表上方，用户不点开表格也能抓到重点
  summary?: string;
  // 图表说明书：渲染为结果表下方的柱状 / 折线图
  chart?: ChartSpec;
  // 可解释信息：渲染为结果表下方的可折叠面板
  explain?: ExplainInfo;
  // 权限提示：渲染为结果上方的角色/过滤/脱敏提示条
  permission?: PermissionInfo;
};
