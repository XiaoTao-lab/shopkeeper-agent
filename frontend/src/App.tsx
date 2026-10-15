/**
 * 前端应用主组件
 * 负责聊天会话状态、SSE 事件消费和整体页面布局
 */
import {
  Activity,
  BarChart3,
  BookOpen,
  Eraser,
  Leaf,
  LogIn,
  LogOut,
  Server,
  ShieldCheck,
} from "lucide-react";
import { type FormEvent, useState } from "react";
import { Composer } from "./components/Composer";
import { EmptyState } from "./components/EmptyState";
import { GlossaryPanel } from "./components/GlossaryPanel";
import { MessageBubble } from "./components/MessageBubble";
import { SessionSidebar } from "./components/SessionSidebar";
import { fetchToken } from "./lib/agentApi";
import { useChatSession } from "./hooks/useChatSession";
import { cn } from "./lib/format";
import { DEMO_USERS, roleLabel } from "./lib/roles";

const examples = [
  "统计 2025 年第一季度各大区的 GMV，并按 GMV 从高到低排序",
  "统计 2025 年 3 月各商品品类的销量和销售额",
  "查询华东地区 2025 年第一季度销售额最高的前 5 个商品",
  "按会员等级统计 2025 年第一季度的订单数和销售额",
];

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "Vite /api proxy";

/**
 * 登录态的本地暂存。
 *
 * 用 sessionStorage 而不是 localStorage：关掉标签页即失效，缩小令牌泄露窗口。
 * 生产环境更推荐 httpOnly + Secure Cookie —— 令牌完全不落到 JS 手里，
 * 从根上避免 XSS 直接把令牌读走。这里为方便本地演示做折中。
 */
const TOKEN_STORAGE_KEY = "shopkeeper-agent.session";

type StoredSession = {
  token: string;
  username: string;
  /** 角色由服务端签发令牌时写定，前端只负责展示，无权修改 */
  role: string;
};

function loadStoredSession(): StoredSession | null {
  try {
    const raw = sessionStorage.getItem(TOKEN_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as StoredSession) : null;
  } catch {
    return null;
  }
}

export default function App() {
  const [draft, setDraft] = useState("");
  // 登录态：令牌 + 服务端认定的身份。账号和角色都来自后端响应——
  // 前端只能选"用哪个账号登录"，不能选"我要什么权限"，这是本次鉴权改造的核心。
  const [session, setSession] = useState<StoredSession | null>(() => loadStoredSession());
  const [loginName, setLoginName] = useState(DEMO_USERS[0].value);
  const [loginPassword, setLoginPassword] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const [showGlossary, setShowGlossary] = useState(false);

  const token = session?.token ?? null;
  const username = session?.username ?? null;

  const {
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
  } = useChatSession(token, username);

  const canSubmit = draft.trim().length > 0 && !isStreaming;

  const persistSession = (next: StoredSession | null) => {
    setSession(next);
    try {
      if (next) sessionStorage.setItem(TOKEN_STORAGE_KEY, JSON.stringify(next));
      else sessionStorage.removeItem(TOKEN_STORAGE_KEY);
    } catch {
      // 隐私模式等场景下 sessionStorage 可能不可写；写不进去不影响本次会话继续用
    }
  };

  const handleLogin = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (loggingIn) return;

    setLoggingIn(true);
    setAuthError(null);
    try {
      // 请求体里只有账号和口令，没有角色字段：角色由服务端查表后写进令牌
      const result = await fetchToken(loginName, loginPassword);
      persistSession({
        token: result.access_token,
        username: result.username,
        role: result.role,
      });
      setLoginPassword("");
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : String(error));
    } finally {
      setLoggingIn(false);
    }
  };

  const handleLogout = () => {
    persistSession(null);
    setAuthError(null);
  };

  const handleNewSession = async () => {
    if (isStreaming) return;
    const id = await createNewSession();
    if (id) {
      setDraft("");
    }
  };

  const handleSend = () => {
    const query = draft.trim();
    if (!query || isStreaming) return;
    sendQuery(query);
    setDraft("");
  };

  const handlePickClarify = (text: string) => {
    setDraft(text);
  };

  return (
    <div className="h-dvh overflow-hidden bg-parchment text-ink">
      <div className="pointer-events-none fixed inset-0 bg-[linear-gradient(90deg,rgba(32,32,29,0.045)_1px,transparent_1px),linear-gradient(rgba(32,32,29,0.035)_1px,transparent_1px)] bg-[size:48px_48px]" />
      <div className="pointer-events-none fixed inset-0 grain" />

      <div className="relative grid h-full min-h-0 overflow-hidden lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="hidden min-h-0 border-r border-ink/10 bg-[#efe6d8]/85 backdrop-blur lg:flex lg:flex-col">
          <SessionSidebar
            sessions={view.sessions}
            activeSessionId={view.activeSessionId}
            isStreaming={isStreaming}
            onSelect={switchSession}
            onNew={handleNewSession}
            onRename={rename}
            onDelete={remove}
          />

          <div className="border-t border-ink/10 p-4">
            <div className="grid gap-2 text-xs text-ink/55">
              <div className="flex items-center justify-between gap-3">
                <span className="inline-flex items-center gap-2">
                  <Server className="h-3.5 w-3.5" aria-hidden="true" />
                  API
                </span>
                <span className="truncate font-mono">{API_BASE_URL}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="inline-flex items-center gap-2">
                  <Activity className="h-3.5 w-3.5" aria-hidden="true" />
                  会话
                </span>
                <span>{view.sessions.length}</span>
              </div>
            </div>
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          <header className="flex h-16 shrink-0 items-center justify-between border-b border-ink/10 bg-parchment/88 px-4 backdrop-blur lg:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <div className="grid h-9 w-9 shrink-0 place-items-center bg-moss text-white lg:hidden">
                <BarChart3 className="h-4 w-4" aria-hidden="true" />
              </div>
              <div className="min-w-0">
                <div className="truncate text-sm font-semibold text-ink">智能数据分析 Agent</div>
                <div className="truncate text-xs text-ink/45">FastAPI SSE / LangGraph</div>
              </div>
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              {session ? (
                <div className="inline-flex items-center gap-2 border border-ink/15 bg-white/55 px-2.5 py-1.5 text-xs text-ink/70">
                  <ShieldCheck className="h-3.5 w-3.5 text-moss" aria-hidden="true" />
                  <span className="font-mono text-ink/80">{session.username}</span>
                  <span className="text-ink/35">/</span>
                  {/* 角色来自令牌 claim，前端只负责展示，改不了 */}
                  <span className="font-medium text-ink">{roleLabel(session.role)}</span>
                  {session.role === "admin" ? (
                    <button
                      type="button"
                      onClick={() => setShowGlossary(true)}
                      className="inline-flex items-center gap-1 border-l border-ink/15 pl-1.5 text-ink/60 transition hover:text-ink"
                      title="业务字典管理"
                    >
                      <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
                      业务字典
                    </button>
                  ) : null}
                  <button
                    type="button"
                    onClick={handleLogout}
                    disabled={isStreaming}
                    className="ml-1 inline-flex items-center gap-1 text-ink/45 transition hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                    title="退出登录"
                  >
                    <LogOut className="h-3.5 w-3.5" aria-hidden="true" />
                    退出
                  </button>
                </div>
              ) : (
                <form
                  onSubmit={handleLogin}
                  className="inline-flex items-center gap-1.5 border border-ink/15 bg-white/55 px-2.5 py-1.5 text-xs text-ink/70"
                >
                  <ShieldCheck className="h-3.5 w-3.5 text-moss" aria-hidden="true" />
                  <select
                    value={loginName}
                    onChange={(event) => setLoginName(event.target.value)}
                    disabled={loggingIn}
                    className="bg-transparent text-xs text-ink outline-none disabled:cursor-not-allowed"
                    aria-label="选择登录账号"
                  >
                    {/* 选的是"账号"，不是"角色"——角色由服务端按账号查表决定 */}
                    {DEMO_USERS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <input
                    type="password"
                    value={loginPassword}
                    onChange={(event) => setLoginPassword(event.target.value)}
                    disabled={loggingIn}
                    placeholder="口令"
                    autoComplete="current-password"
                    className="w-20 border-l border-ink/15 bg-transparent pl-1.5 text-xs text-ink outline-none placeholder:text-ink/35 disabled:cursor-not-allowed"
                    aria-label="口令"
                  />
                  <button
                    type="submit"
                    disabled={loggingIn || loginPassword.length === 0}
                    className="inline-flex items-center gap-1 bg-ink px-2 py-0.5 font-medium text-parchment transition hover:bg-soot disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <LogIn className="h-3 w-3" aria-hidden="true" />
                    {loggingIn ? "登录中" : "登录"}
                  </button>
                </form>
              )}
              <button
                type="button"
                onClick={() => {
                  if (view.messages.length === 0 || isStreaming) return;
                  // 清空当前视图（不删除会话）
                }}
                disabled={view.messages.length === 0 || isStreaming}
                className={cn(
                  "grid h-9 w-9 place-items-center rounded-full text-ink/55 transition hover:bg-ink/5 hover:text-ink disabled:cursor-not-allowed disabled:opacity-35",
                )}
                title="清空"
                aria-label="清空"
              >
                <Eraser className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </header>

          {authError ? (
            <div className="shrink-0 border-b border-l-2 border-ink/10 border-l-[#b3452e] bg-[#f6e7e2] px-4 py-2 text-xs text-ink/80 lg:px-6">
              <span className="inline-flex items-center gap-2">
                <ShieldCheck className="h-3.5 w-3.5" aria-hidden="true" />
                {authError}
              </span>
            </div>
          ) : null}

          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            {view.messages.length === 0 && !view.loadingHistory ? (
              <EmptyState examples={examples} onUseExample={(example) => setDraft(example)} />
            ) : (
              <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-6 lg:px-8">
                {view.messages.map((message) => (
                  <MessageBubble
                    key={message.id}
                    message={message}
                    onPickClarify={handlePickClarify}
                    token={token}
                    sessionId={view.activeSessionId}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="border-t border-ink/10 bg-[#efe6d8]/45 px-4 py-2 text-center text-xs text-ink/45">
            <span className="inline-flex items-center gap-2">
              <Leaf className="h-3.5 w-3.5 text-moss" aria-hidden="true" />
              {isStreaming ? "运行中" : view.activeSessionId ? "就绪" : "请先登录并创建会话"}
            </span>
          </div>
          <Composer
            value={draft}
            disabled={!canSubmit || !view.activeSessionId}
            isStreaming={isStreaming}
            onChange={setDraft}
            onSubmit={handleSend}
            onStop={stopQuery}
          />
        </main>
        {showGlossary && token ? (
          <GlossaryPanel token={token} onClose={() => setShowGlossary(false)} />
        ) : null}
      </div>
    </div>
  );
}
