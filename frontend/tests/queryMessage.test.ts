import { strict as assert } from "node:assert";
import { test } from "node:test";

import { applyResultEvent, applyTerminalRun, recoveryLookup } from "../src/lib/queryMessage";
import type { ChatMessage, ResultSnapshot, RunRecord } from "../src/types/agent";

test("result event keeps the run id required for CSV download", () => {
  const result: ResultSnapshot = {
    type: "result",
    schema_version: 1,
    columns: [{ key: "sales", label: "销售额", type: "decimal", encoding: "string" }],
    data: [{ sales: "10.00" }],
    returned_row_count: 1,
    truncated: false,
  };
  const message: ChatMessage = {
    id: "assistant-1",
    role: "assistant",
    content: "正在查询",
    createdAt: 1,
    status: "streaming",
    steps: [],
  };

  const updated = applyResultEvent(message, result, "run-123");

  assert.equal(updated.status, "done");
  assert.equal(updated.run_id, "run-123");
  assert.equal(updated.result, result);
});

test("recovery falls back to the client request id when the meta event was lost", () => {
  assert.deepEqual(
    recoveryLookup("run-123", "request-456"),
    { kind: "run", id: "run-123" },
  );
  assert.deepEqual(
    recoveryLookup(null, "request-456"),
    { kind: "request", id: "request-456" },
  );
});

test("recovery renders an interrupted terminal run instead of leaving the message confirming", () => {
  const message: ChatMessage = {
    id: "assistant-1",
    role: "assistant",
    content: "正在核对结果...",
    createdAt: 1,
    status: "confirming",
  };
  const run: RunRecord = {
    id: "run-123",
    session_id: "session-123",
    client_request_id: "request-123",
    raw_query: "销售额",
    rewritten_query: null,
    status: "interrupted",
    outcome: "error",
    terminal_event: { type: "error", message: "查询超过执行期限，请重新提问。" },
    created_at: "2026-10-15T00:00:00Z",
    deadline_at: "2026-10-15T00:05:00Z",
    finished_at: "2026-10-15T00:05:00Z",
    end_reason: "deadline_exceeded",
  };

  const updated = applyTerminalRun(message, run);

  assert.equal(updated.status, "interrupted");
  assert.equal(updated.error, "查询超过执行期限，请重新提问。");
  assert.equal(updated.run_id, "run-123");
});

test("a still-running recovered run remains confirming", () => {
  const message: ChatMessage = {
    id: "assistant-1",
    role: "assistant",
    content: "正在核对结果...",
    createdAt: 1,
    status: "confirming",
  };
  const run: RunRecord = {
    id: "run-123",
    session_id: "session-123",
    client_request_id: "request-123",
    raw_query: "销售额",
    rewritten_query: null,
    status: "running",
    outcome: null,
    terminal_event: null,
    created_at: "2026-10-15T00:00:00Z",
    deadline_at: "2026-10-15T00:05:00Z",
    finished_at: null,
    end_reason: null,
  };

  assert.equal(applyTerminalRun(message, run).status, "confirming");
});
