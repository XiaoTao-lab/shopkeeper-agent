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

// 结果列描述：有序、带类型，用于表格渲染、排序、CSV 导出
export type ResultColumn = {
  key: string;
  label: string;
  type: "string" | "decimal" | "integer" | "number" | "boolean" | "date" | "datetime" | "unknown";
  encoding: "string" | "json";
};

// 结构化结果快照：包含有序列定义和数据行
export type ResultSnapshot = {
  type: "result";
  schema_version: 1;
  columns: ResultColumn[];
  data: Array<Record<string, unknown>>;
  returned_row_count: number;
  truncated: boolean;
  empty?: boolean;
  message?: string;
  summary?: string;
  chart?: ChartSpec;
  explain?: ExplainInfo;
  permission?: PermissionInfo;
};

export type ResultEvent = ResultSnapshot;

export type ErrorEvent = {
  type: "error";
  message: string;
};

// 会话元信息：后端在流开始时下发本次会话 id 和 run_id，用于多轮续接和状态追踪
export type MetaEvent = {
  type: "meta";
  thread_id: string;
  run_id: string;
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

// 终态事件：标识一次查询完成
export type DoneEvent = {
  type: "done";
  run_id: string;
  status: "completed" | "failed" | "interrupted";
  outcome: "result" | "empty" | "clarify" | "blocked" | "error";
};

export type AgentEvent =
  | ProgressEvent
  | ResultEvent
  | ErrorEvent
  | MetaEvent
  | ClarifyEvent
  | DoneEvent;

export type StepState = {
  step: string;
  status: ProgressStatus;
  updatedAt: number;
};

// 会话摘要（列表用）
export type SessionSummary = {
  id: string;
  title: string | null;
  scope_changed: boolean;
  created_at: string;
  updated_at: string;
};

// 查询运行记录
export type RunRecord = {
  id: string;
  session_id: string;
  client_request_id: string;
  raw_query: string;
  rewritten_query: string | null;
  status: "running" | "completed" | "failed" | "interrupted";
  outcome: "result" | "empty" | "clarify" | "blocked" | "error" | null;
  terminal_event: ResultSnapshot | ClarifyEvent | ErrorEvent | null;
  created_at: string;
  deadline_at: string;
  finished_at: string | null;
  end_reason: string | null;
};

// 聊天消息：一条用户问题 + 助手回复
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: number;
  status?: "streaming" | "done" | "error" | "confirming" | "interrupted";
  steps?: StepState[];
  result?: ResultSnapshot;
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
  // 关联的 run_id，用于状态查询和 CSV 导出
  run_id?: string;
};
