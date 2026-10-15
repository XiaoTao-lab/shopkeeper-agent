/**
 * 会话侧边栏组件
 * 显示历史会话列表，支持新建、切换、重命名、删除
 */
import { History, MessageSquarePlus, MoreHorizontal, Pencil, Trash2, X } from "lucide-react";
import { useState } from "react";
import type { SessionSummary } from "../types/agent";

type SessionSidebarProps = {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  isStreaming: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
};

export function SessionSidebar({
  sessions,
  activeSessionId,
  isStreaming,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: SessionSidebarProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [menuId, setMenuId] = useState<string | null>(null);

  const startRename = (session: SessionSummary) => {
    setEditingId(session.id);
    setEditTitle(session.title ?? "新会话");
    setMenuId(null);
  };

  const commitRename = () => {
    if (editingId && editTitle.trim()) {
      onRename(editingId, editTitle.trim());
    }
    setEditingId(null);
  };

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-ink/10 px-5 py-5">
        <div className="flex items-center gap-3">
          <div className="grid h-10 w-10 place-items-center bg-ink text-parchment">
            <History className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <div className="text-base font-semibold tracking-[0.02em]">历史会话</div>
            <div className="text-xs text-ink/50">{sessions.length} 个会话</div>
          </div>
        </div>
      </div>

      <div className="border-b border-ink/10 p-4">
        <button
          type="button"
          onClick={onNew}
          disabled={isStreaming}
          className="flex h-11 w-full items-center justify-center gap-2 bg-ink text-sm font-semibold text-parchment transition hover:bg-soot disabled:cursor-not-allowed disabled:bg-ink/35"
        >
          <MessageSquarePlus className="h-4 w-4" aria-hidden="true" />
          新会话
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {sessions.length === 0 ? (
          <div className="px-3 py-8 text-center text-xs text-ink/40">暂无历史会话</div>
        ) : (
          <div className="space-y-1">
            {sessions.map((session) => {
              const isActive = session.id === activeSessionId;
              const isEditing = editingId === session.id;
              const showMenu = menuId === session.id;

              return (
                <div
                  key={session.id}
                  className={`group relative rounded-lg border px-3 py-2.5 text-sm transition ${
                    isActive
                      ? "border-moss/40 bg-moss/10 text-ink"
                      : "border-transparent text-ink/70 hover:border-ink/10 hover:bg-white/40"
                  } ${session.scope_changed ? "opacity-50" : ""}`}
                >
                  {isEditing ? (
                    <div className="flex items-center gap-1">
                      <input
                        type="text"
                        value={editTitle}
                        onChange={(e) => setEditTitle(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") commitRename();
                          if (e.key === "Escape") setEditingId(null);
                        }}
                        autoFocus
                        className="flex-1 bg-transparent text-sm text-ink outline-none"
                        maxLength={100}
                      />
                      <button
                        type="button"
                        onClick={commitRename}
                        className="text-moss hover:text-moss/70"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditingId(null)}
                        className="text-ink/40 hover:text-ink/70"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => !session.scope_changed && onSelect(session.id)}
                      disabled={session.scope_changed || isStreaming}
                      className="block w-full truncate text-left disabled:cursor-not-allowed"
                      title={session.scope_changed ? "权限已变更，无法查看" : session.title ?? "新会话"}
                    >
                      {session.scope_changed ? (
                        <span className="text-ink/40">[权限已变更]</span>
                      ) : (
                        session.title ?? "新会话"
                      )}
                    </button>
                  )}

                  {!isEditing && !session.scope_changed && (
                    <div className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 transition group-hover:opacity-100">
                      <button
                        type="button"
                        onClick={() => setMenuId(showMenu ? null : session.id)}
                        className="rounded p-1 text-ink/40 hover:bg-ink/5 hover:text-ink"
                      >
                        <MoreHorizontal className="h-3.5 w-3.5" />
                      </button>
                      {showMenu && (
                        <div className="absolute right-0 top-full z-10 mt-1 w-28 rounded-md border border-ink/10 bg-white py-1 shadow-lg">
                          <button
                            type="button"
                            onClick={() => startRename(session)}
                            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-ink/70 hover:bg-ink/5"
                          >
                            <Pencil className="h-3 w-3" />
                            重命名
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              onDelete(session.id);
                              setMenuId(null);
                            }}
                            className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-tomato hover:bg-tomato/5"
                          >
                            <Trash2 className="h-3 w-3" />
                            删除
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
