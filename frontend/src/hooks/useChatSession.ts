/**
 * 会话与请求生命周期 Hook
 *
 * 管理会话列表、当前会话、消息历史、加载状态。
 * 处理账号切换清理、断流恢复、请求键幂等。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  type AgentEvent,
  type ChatMessage,
  type ClarifyEvent,
  type ErrorEvent,
  type ResultSnapshot,
  type RunRecord,
  type SessionSummary,
} from "../types/agent";
import {
  ConflictError,
  createSession,
  deleteSession,
  getRun,
  getRunByRequest,
  listRuns,
  listSessions,
  renameSession,
  streamQuery,
  UnauthorizedError,
} from "../lib/agentApi";
import { applyResultEvent, applyTerminalRun, recoveryLookup } from "../lib/queryMessage";

type SessionView = {
  activeSessionId: string | null;
  sessions: SessionSummary[];
  messages: ChatMessage[];
  loadingHistory: boolean;
};

type PendingSubmit = {
  clientRequestId: string;
  query: string;
  sessionId: string;
};

const PENDING_KEY = "shopkeeper-agent.pending";

function loadPending(): PendingSubmit | null {
  try {
    const raw = sessionStorage.getItem(PENDING_KEY);
    return raw ? (JSON.parse(raw) as PendingSubmit) : null;
  } catch {
    return null;
  }
}

function savePending(pending: PendingSubmit | null) {
  try {
    if (pending) sessionStorage.setItem(PENDING_KEY, JSON.stringify(pending));
    else sessionStorage.removeItem(PENDING_KEY);
  } catch {
    // ignore
  }
}

function makeId(): string {
  return crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function useChatSession(token: string | null, username: string | null) {
  const [view, setView] = useState<SessionView>({
    activeSessionId: null,
    sessions: [],
    messages: [],
    loadingHistory: false,
  });
  const [isStreaming, setIsStreaming] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);

  // 加载会话列表
  const loadSessions = useCallback(async () => {
    if (!token) return;
    try {
      const result = await listSessions(token);
      setView((v) => ({ ...v, sessions: result.items }));
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        setAuthError("登录状态已失效，请重新登录。");
      }
    }
  }, [token]);

  // 加载会话历史
  const loadSessionHistory = useCallback(async (sessionId: string) => {
    if (!token) return;
    setView((v) => ({ ...v, loadingHistory: true }));
    try {
      const result = await listRuns(token, sessionId);
      const messages: ChatMessage[] = [];
      for (const run of result.items) {
        // 用户消息气泡
        messages.push({
          id: `user-${run.id}`,
          role: "user",
          content: run.raw_query,
          createdAt: new Date(run.created_at).getTime(),
          status: "done",
        });
        // 助手回复气泡
        const assistantMsg = applyTerminalRun({
          id: `assistant-${run.id}`,
          role: "assistant",
          content: "正在核对结果...",
          createdAt: new Date(run.created_at).getTime(),
          status: "confirming",
        }, run);
        messages.push(assistantMsg);
      }
      setView((v) => ({
        ...v,
        activeSessionId: sessionId,
        messages,
        loadingHistory: false,
      }));
    } catch (err) {
      setView((v) => ({ ...v, loadingHistory: false }));
      if (err instanceof UnauthorizedError) {
        setAuthError("登录状态已失效，请重新登录。");
      }
    }
  }, [token]);

  // 创建新会话
  const createNewSession = useCallback(async () => {
    if (!token) return null;
    try {
      const session = await createSession(token);
      setView((v) => ({
        ...v,
        activeSessionId: session.id,
        sessions: [session, ...v.sessions],
        messages: [],
      }));
      return session.id;
    } catch {
      return null;
    }
  }, [token]);

  // 切换会话
  const switchSession = useCallback(
    async (sessionId: string) => {
      // 终止当前流
      abortRef.current?.abort();
      generationRef.current++;
      setIsStreaming(false);
      savePending(null);
      await loadSessionHistory(sessionId);
    },
    [loadSessionHistory],
  );

  // 重命名会话
  const rename = useCallback(
    async (sessionId: string, title: string) => {
      if (!token) return;
      try {
        const updated = await renameSession(token, sessionId, title);
        setView((v) => ({
          ...v,
          sessions: v.sessions.map((s) => (s.id === sessionId ? updated : s)),
        }));
      } catch {
        // ignore
      }
    },
    [token],
  );

  // 删除会话
  const remove = useCallback(
    async (sessionId: string) => {
      if (!token) return;
      try {
        await deleteSession(token, sessionId);
        setView((v) => ({
          ...v,
          sessions: v.sessions.filter((s) => s.id !== sessionId),
          activeSessionId: v.activeSessionId === sessionId ? null : v.activeSessionId,
          messages: v.activeSessionId === sessionId ? [] : v.messages,
        }));
      } catch {
        // ignore
      }
    },
    [token],
  );

  // 发送查询
  const sendQuery = useCallback(
    async (query: string, existingClientRequestId?: string) => {
      const currentToken = token;
      if (!currentToken || !view.activeSessionId) {
        // 自动创建会话
        const sessionId = await createNewSession();
        if (!sessionId) return;
      }

      const sessionId = view.activeSessionId!;
      const clientRequestId = existingClientRequestId ?? makeId();
      const pending: PendingSubmit = { clientRequestId, query, sessionId };
      savePending(pending);

      const userMessage: ChatMessage = {
        id: makeId(),
        role: "user",
        content: query,
        createdAt: Date.now(),
      };
      const assistantId = makeId();
      const assistantMessage: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "正在连接问数智能体...",
        createdAt: Date.now(),
        status: "streaming",
        steps: [],
      };

      const controller = new AbortController();
      abortRef.current = controller;
      setIsStreaming(true);
      generationRef.current++;
      const currentGeneration = generationRef.current;

      // 追踪后端返回的 run_id
      let runId: string | null = null;

      setView((v) => ({
        ...v,
        messages: [...v.messages, userMessage, assistantMessage],
      }));

      const onEvent = (event: AgentEvent) => {
        if (generationRef.current !== currentGeneration) return;

        if (event.type === "meta") {
          runId = event.run_id;
          return;
        }

        if (event.type === "progress") {
          setView((v) => ({
            ...v,
            messages: v.messages.map((msg) => {
              if (msg.id !== assistantId) return msg;
              if (msg.status === "done" || msg.status === "error") {
                return { ...msg, steps: [...(msg.steps ?? []).filter((s) => s.step !== event.step), { step: event.step, status: event.status, updatedAt: Date.now() }] };
              }
              return {
                ...msg,
                content: event.status === "running" ? `正在执行：${event.step}` : msg.content,
                steps: [...(msg.steps ?? []).filter((s) => s.step !== event.step), { step: event.step, status: event.status, updatedAt: Date.now() }],
              };
            }),
          }));
          return;
        }

        if (event.type === "result") {
          setView((v) => ({
            ...v,
            messages: v.messages.map((msg) => {
              if (msg.id !== assistantId) return msg;
              return applyResultEvent(msg, event, runId);
            }),
          }));
          return;
        }

        if (event.type === "clarify") {
          setView((v) => ({
            ...v,
            messages: v.messages.map((msg) => {
              if (msg.id !== assistantId) return msg;
              return { ...msg, status: "done" as const, content: event.message, clarify: event.options ?? [] };
            }),
          }));
          return;
        }

        if (event.type === "done") {
          runId ??= event.run_id;
          savePending(null);
          setIsStreaming(false);
          // 刷新会话列表（标题可能更新）
          loadSessions();
          // 兜底：如果 result/clarify/error 都没到，消息还卡在 streaming，
          // 主动去后端查 run 状态，避免永远显示"处理中"。
          if (currentToken) {
            (async () => {
              try {
                const lookup = recoveryLookup(runId, clientRequestId);
                const run = lookup.kind === "run"
                  ? await getRun(currentToken, sessionId, lookup.id)
                  : await getRunByRequest(currentToken, sessionId, lookup.id);
                if (run.status !== "running") {
                  setView((v) => ({
                    ...v,
                    messages: v.messages.map((msg) => {
                      if (msg.id !== assistantId) return msg;
                      if (msg.status !== "streaming") return msg;
                      return applyTerminalRun(msg, run);
                    }),
                  }));
                }
              } catch {
                // ignore
              }
            })();
          }
          return;
        }

        if (event.type === "error") {
          setView((v) => ({
            ...v,
            messages: v.messages.map((msg) => {
              if (msg.id !== assistantId) return msg;
              return { ...msg, status: "error" as const, content: "这次查询没有成功。", error: event.message };
            }),
          }));
        }
      };

      try {
        await streamQuery(query, {
          signal: controller.signal,
          threadId: sessionId,
          clientRequestId,
          token: currentToken!,
          onEvent,
        });
        // 流结束但没有 done 事件
        setView((v) => ({
          ...v,
          messages: v.messages.map((msg) => {
            if (msg.id !== assistantId) return msg;
            if (msg.status === "streaming") {
              return { ...msg, status: "confirming" as const, content: "连接中断，正在核对结果..." };
            }
            return msg;
          }),
        }));
        // 尝试查询状态
        if (currentToken) {
          try {
            const lookup = recoveryLookup(runId, clientRequestId);
            const run = lookup.kind === "run"
              ? await getRun(currentToken, sessionId, lookup.id)
              : await getRunByRequest(currentToken, sessionId, lookup.id);
            if (run.status !== "running") {
              setView((v) => ({
                ...v,
                messages: v.messages.map((msg) => {
                  if (msg.id !== assistantId) return msg;
                  return applyTerminalRun(msg, run);
                }),
              }));
            }
          } catch {
            // ignore
          }
        }
      } catch (error) {
        const isAbort = error instanceof DOMException && error.name === "AbortError";
        const isUnauthorized = error instanceof UnauthorizedError;
        const isConflict = error instanceof ConflictError;

        if (isUnauthorized) {
          setAuthError("登录状态已失效，请重新登录。");
        }

        if (isConflict) {
          // 重复提交，查询状态
          try {
            const runId = error.details.run_id as string;
            if (runId && currentToken) {
              const run = await getRun(currentToken, sessionId, runId);
              if (run.status === "completed" && run.terminal_event) {
                const snapshot = run.terminal_event;
                const isResult = snapshot && "type" in snapshot && snapshot.type === "result";
                setView((v) => ({
                  ...v,
                  messages: v.messages.map((msg) => {
                    if (msg.id !== assistantId) return msg;
                    if (isResult) {
                      const result = snapshot as ResultSnapshot;
                      return {
                        ...msg,
                        status: "done" as const,
                        content: result.message ?? "查询完成",
                        result,
                        summary: result.summary,
                        chart: result.chart,
                        explain: result.explain,
                        permission: result.permission,
                        run_id: run.id,
                      };
                    }
                    if (snapshot && "type" in snapshot && snapshot.type === "clarify") {
                      return { ...msg, status: "done" as const, content: snapshot.message, clarify: snapshot.options ?? [] };
                    }
                    if (snapshot && "type" in snapshot && snapshot.type === "error") {
                      return { ...msg, status: "error" as const, content: "这次查询没有成功。", error: snapshot.message };
                    }
                    return { ...msg, status: "done" as const, content: "查询完成", run_id: run.id };
                  }),
                }));
              }
            }
          } catch {
            // ignore
          }
        } else if (!isAbort) {
          setView((v) => ({
            ...v,
            messages: v.messages.map((msg) => {
              if (msg.id !== assistantId) return msg;
              return {
                ...msg,
                status: "error" as const,
                content: isUnauthorized ? "登录状态已失效，请重新登录后重试。" : "无法连接问数接口。",
                error: error instanceof Error ? error.message : String(error),
              };
            }),
          }));
        }
      } finally {
        if (!isStreaming || abortRef.current === controller) {
          setIsStreaming(false);
          abortRef.current = null;
        }
      }
    },
    [token, view.activeSessionId, createNewSession, loadSessions],
  );

  // 停止查询：只读核对已有请求，绝不因为停止动作再次 POST 执行查询。
  const stopQuery = useCallback(() => {
    const pending = loadPending();
    generationRef.current++;
    abortRef.current?.abort();
    setIsStreaming(false);
    setView((v) => ({
      ...v,
      messages: v.messages.map((message) =>
        message.status === "streaming"
          ? { ...message, status: "confirming" as const, content: "已停止连接，正在核对结果..." }
          : message,
      ),
    }));
    if (!token || !pending) return;
    void getRunByRequest(token, pending.sessionId, pending.clientRequestId)
      .then((run) => {
        if (run.status === "running") return;
        setView((v) => ({
          ...v,
          messages: v.messages.map((message) =>
            message.status === "confirming" ? applyTerminalRun(message, run) : message,
          ),
        }));
        savePending(null);
      })
      .catch(() => {
        // 原请求可能仍在网络中登记；保留请求键给刷新后的只读恢复流程。
      });
  }, [token]);

  // 账号切换时清理
  useEffect(() => {
    abortRef.current?.abort();
    generationRef.current++;
    setIsStreaming(false);
    savePending(null);
    setView({
      activeSessionId: null,
      sessions: [],
      messages: [],
      loadingHistory: false,
    });
    setAuthError(null);
    if (token) {
      loadSessions();
    }
  }, [token, username, loadSessions]);

  // 页面加载时只读恢复 pending 提交；不自动重发执行型 POST。
  useEffect(() => {
    if (!token) return;
    const pending = loadPending();
    if (!pending?.sessionId) return;
    let cancelled = false;
    void getRunByRequest(token, pending.sessionId, pending.clientRequestId)
      .then(async (run) => {
        if (cancelled) return;
        await loadSessionHistory(pending.sessionId);
        if (cancelled) return;
        if (run.status !== "running") savePending(null);
      })
      .catch(() => {
        // 404 表示原请求尚未登记或已不可见；不能由恢复流程创建一轮新查询。
      });
    return () => {
      cancelled = true;
    };
  }, [token, loadSessionHistory]);

  return {
    view,
    isStreaming,
    authError,
    setAuthError,
    loadSessions,
    loadSessionHistory,
    createNewSession,
    switchSession,
    rename,
    remove,
    sendQuery,
    stopQuery,
  };
}
