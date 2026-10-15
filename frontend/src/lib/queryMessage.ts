import type { ChatMessage, ResultSnapshot, RunRecord } from "../types/agent";

export type RecoveryLookup =
  | { kind: "run"; id: string }
  | { kind: "request"; id: string };

/**
 * SSE 的首条 meta 可能在网络中丢失。已有 run_id 时精确按 run 查询；
 * 否则必须复用同一 client_request_id 做只读查询，不能生成新键再次执行。
 */
export function recoveryLookup(
  runId: string | null,
  clientRequestId: string,
): RecoveryLookup {
  return runId
    ? { kind: "run", id: runId }
    : { kind: "request", id: clientRequestId };
}

/** Convert the persisted terminal snapshot into the single assistant bubble. */
export function applyTerminalRun(message: ChatMessage, run: RunRecord): ChatMessage {
  const snapshot = run.terminal_event;
  const base: ChatMessage = {
    ...message,
    run_id: run.id,
    status:
      run.status === "running"
        ? "confirming"
        : run.status === "completed"
        ? "done"
        : run.status === "interrupted"
          ? "interrupted"
          : "error",
  };

  if (!snapshot) {
    return {
      ...base,
      content: run.status === "running" ? "正在核对结果..." : "查询已结束。",
    };
  }
  if (snapshot.type === "result") {
    return {
      ...base,
      status: "done",
      content: snapshot.empty && snapshot.message ? snapshot.message : "查询完成",
      result: snapshot,
      summary: snapshot.summary,
      chart: snapshot.chart,
      explain: snapshot.explain,
      permission: snapshot.permission,
    };
  }
  if (snapshot.type === "clarify") {
    return {
      ...base,
      status: "done",
      content: snapshot.message,
      clarify: snapshot.options ?? [],
    };
  }
  return {
    ...base,
    content: "这次查询没有成功。",
    error: snapshot.message,
  };
}

/**
 * 将一次已收到的结果与其 run_id 绑定。
 * run_id 来自同一 SSE 流中更早的 meta 事件，是 CSV 导出和状态恢复的资源标识。
 */
export function applyResultEvent(
  message: ChatMessage,
  result: ResultSnapshot,
  runId: string | null,
): ChatMessage {
  return {
    ...message,
    status: "done",
    content: result.empty && result.message ? result.message : "查询完成",
    result,
    summary: result.summary,
    chart: result.chart,
    explain: result.explain,
    permission: result.permission,
    ...(runId ? { run_id: runId } : {}),
  };
}
