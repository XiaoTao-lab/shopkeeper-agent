/**
 * 智能体接口客户端
 * 封装登录换令牌、会话 CRUD、/api/query SSE 流式请求与事件解析逻辑
 *
 * 身份传递方式的变化：
 * 此前是请求头 `X-User-Role: admin`，角色由前端自己填——等于客户端自报权限；
 * 现在改为 `Authorization: Bearer <token>`，令牌由后端签发，
 * 前端只能"拿着"，改内容签名就对不上，也无权决定自己能拿到什么角色。
 */

import type { AgentEvent, RunRecord, SessionSummary } from "../types/agent";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, "") ?? "";

/**
 * 令牌失效（缺失 / 过期 / 签名不符）时抛出。
 * 与普通网络错误区分开，调用方据此提示"重新登录"而不是"网络故障"。
 */
export class UnauthorizedError extends Error {
  constructor(message = "登录状态已失效，请重新登录") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

/** 结构化 409 错误 */
export class ConflictError extends Error {
  code: string;
  details: Record<string, unknown>;
  constructor(code: string, details: Record<string, unknown> = {}) {
    super(code);
    this.name = "ConflictError";
    this.code = code;
    this.details = details;
  }
}

export type TokenResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  role: string;
  username: string;
};

/** 读取 FastAPI 错误响应里的 detail 文案，取不到时返回 null */
async function readDetail(response: Response): Promise<string | null> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    return typeof body.detail === "string" ? body.detail : null;
  } catch {
    return null;
  }
}

/** 读取结构化 409 错误 */
async function readConflict(response: Response): Promise<ConflictError> {
  try {
    const body = (await response.json()) as { detail?: { code?: string; [key: string]: unknown } };
    const detail = body.detail;
    const code = detail?.code ?? "UNKNOWN_CONFLICT";
    const { code: _, ...rest } = detail ?? {};
    return new ConflictError(code, rest);
  } catch {
    return new ConflictError("UNKNOWN_CONFLICT");
  }
}

/**
 * 用账号 + 口令换取访问令牌
 *
 * 注意请求体里**没有角色字段**：角色由服务端按账号查表得出后写进令牌，
 * 客户端只能选"用哪个账号登录"，不能选"我要什么权限"。
 */
export async function fetchToken(
  username: string,
  password: string,
  signal?: AbortSignal,
): Promise<TokenResponse> {
  const response = await fetch(`${API_BASE_URL}/api/auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
    signal,
  });

  if (!response.ok) {
    const detail = await readDetail(response);
    throw new Error(detail ?? `登录失败：HTTP ${response.status}`);
  }

  return (await response.json()) as TokenResponse;
}

/** 创建新会话 */
export async function createSession(token: string, title = "新会话"): Promise<SessionSummary> {
  const response = await fetch(`${API_BASE_URL}/api/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ title }),
  });
  if (!response.ok) throw new Error(`创建会话失败：HTTP ${response.status}`);
  return (await response.json()) as SessionSummary;
}

/** 获取会话列表 */
export async function listSessions(
  token: string,
  limit = 20,
  cursor?: string,
): Promise<{ items: SessionSummary[]; next_cursor: string | null }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  const response = await fetch(`${API_BASE_URL}/api/sessions?${params}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`获取会话列表失败：HTTP ${response.status}`);
  return (await response.json()) as { items: SessionSummary[]; next_cursor: string | null };
}

/** 获取会话的查询记录 */
export async function listRuns(
  token: string,
  sessionId: string,
  limit = 20,
  cursor?: string,
): Promise<{ session: { id: string; title: string }; items: RunRecord[]; next_cursor: string | null }> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}/runs?${params}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`获取查询记录失败：HTTP ${response.status}`);
  return (await response.json()) as {
    session: { id: string; title: string };
    items: RunRecord[];
    next_cursor: string | null;
  };
}

/** 获取单个查询记录 */
export async function getRun(
  token: string,
  sessionId: string,
  runId: string,
): Promise<RunRecord> {
  const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}/runs/${runId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`获取查询记录失败：HTTP ${response.status}`);
  return (await response.json()) as RunRecord;
}

/** 按请求键查询（只读，不创建新任务） */
export async function getRunByRequest(
  token: string,
  sessionId: string,
  requestId: string,
): Promise<RunRecord> {
  const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}/runs/by-request/${requestId}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (response.status === 404) throw new Error("REQUEST_NOT_REGISTERED");
  if (!response.ok) throw new Error(`查询请求状态失败：HTTP ${response.status}`);
  return (await response.json()) as RunRecord;
}

/** 重命名会话 */
export async function renameSession(token: string, sessionId: string, title: string): Promise<SessionSummary> {
  const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ title }),
  });
  if (!response.ok) throw new Error(`重命名会话失败：HTTP ${response.status}`);
  return (await response.json()) as SessionSummary;
}

/** 删除会话（软删除） */
export async function deleteSession(token: string, sessionId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`删除会话失败：HTTP ${response.status}`);
}

/** 下载 CSV（带鉴权） */
export async function downloadCsv(
  token: string,
  sessionId: string,
  runId: string,
): Promise<Blob> {
  const response = await fetch(`${API_BASE_URL}/api/sessions/${sessionId}/runs/${runId}/export?format=csv`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error(`导出失败：HTTP ${response.status}`);
  return response.blob();
}

type QueryOptions = {
  signal?: AbortSignal;
  /** 多轮会话 id：同一会话各轮传同一个值，后端据此续接对话历史 */
  threadId?: string;
  /** 请求键：防重复提交，同一逻辑提交的所有重发必须复用 */
  clientRequestId?: string;
  /** 访问令牌：由 fetchToken 取得，随 Authorization 头下发 */
  token: string;
  onEvent: (event: AgentEvent) => void;
};

export async function streamQuery(query: string, options: QueryOptions) {
  const response = await fetch(`${API_BASE_URL}/api/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Authorization: `Bearer ${options.token}`,
    },
    body: JSON.stringify({
      query,
      thread_id: options.threadId,
      client_request_id: options.clientRequestId,
    }),
    signal: options.signal,
  });

  // 401 单独处理：这不是"接口坏了"，而是令牌缺失/过期，前端应引导重新登录
  if (response.status === 401) {
    throw new UnauthorizedError();
  }

  // 409 冲突：重复提交、会话忙等
  if (response.status === 409) {
    throw await readConflict(response);
  }

  if (!response.ok) {
    throw new Error(`接口请求失败：HTTP ${response.status}`);
  }

  if (!response.body) {
    throw new Error("浏览器未返回可读取的流式响应。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split(/\n\n/);
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const event = parseSseChunk(chunk);
      if (event) {
        options.onEvent(event);
      }
    }
  }

  buffer += decoder.decode();
  const tail = parseSseChunk(buffer);
  if (tail) {
    options.onEvent(tail);
  }
}

function parseSseChunk(chunk: string): AgentEvent | null {
  const payload = chunk
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.replace(/^data:\s?/, ""))
    .join("\n")
    .trim();

  if (!payload) return null;

  try {
    return JSON.parse(payload) as AgentEvent;
  } catch {
    return {
      type: "error",
      message: `无法解析后端事件：${payload}`,
    };
  }
}
