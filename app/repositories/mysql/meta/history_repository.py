"""Meta MySQL chat history. Every method owns a fresh, short transaction."""
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


class HistoryError(Exception):
    def __init__(self, http_status: int, code: str, **details: Any):
        self.status = http_status
        self.code = code
        self.details = details
        super().__init__(code)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _decode(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _stamp(value: datetime | None) -> str | None:
    return value.isoformat(timespec="microseconds") + "Z" if value else None


def _not_found() -> HistoryError:
    return HistoryError(404, "SESSION_NOT_FOUND")


# 对话历史最多保留的消息条数，与 query_service._encode_context 的截断口径一致
_CONTEXT_LIMIT = 6


def run_view(row: dict) -> dict:
    return {
        "id": row["id"], "session_id": row["session_id"],
        "client_request_id": row["client_request_id"], "raw_query": row["raw_query"],
        "rewritten_query": row["rewritten_query"], "status": row["status"],
        "outcome": row["outcome"], "terminal_event": _decode(row["terminal_event"]),
        "created_at": _stamp(row["created_at"]), "deadline_at": _stamp(row["deadline_at"]),
        "finished_at": _stamp(row["finished_at"]), "end_reason": row["end_reason"],
    }


@dataclass(frozen=True)
class PreparedRun:
    run_id: str
    session_id: str
    client_request_id: str
    raw_query: str
    context: list[dict]
    deadline_at: datetime
    replay: dict | None = None


class HistoryRepository:
    def __init__(self, factory: async_sessionmaker, timeout_seconds: int = 300):
        self.factory = factory
        self.timeout_seconds = timeout_seconds

    @staticmethod
    async def _now(db):
        return (await db.execute(text("SELECT UTC_TIMESTAMP(6)"))).scalar_one()

    @staticmethod
    async def _session(db, sid: str, owner: str, scope: str, *, lock=False, check_scope=True):
        suffix = " FOR UPDATE" if lock else ""
        row = (await db.execute(text("SELECT * FROM chat_session WHERE id=:id AND owner_subject=:owner AND deleted_at IS NULL" + suffix), {"id": sid, "owner": owner.encode("utf-8")})).mappings().first()
        if row is None:
            raise _not_found()
        result = dict(row)
        if check_scope and result["scope_hash"] != scope:
            raise HistoryError(403, "SESSION_SCOPE_CHANGED")
        return result

    @staticmethod
    async def _expire(db, session: dict, now: datetime):
        rid = session["active_run_id"]
        if not rid:
            return
        run = (await db.execute(text("SELECT * FROM query_run WHERE id=:id AND session_id=:sid FOR UPDATE"), {"id": rid, "sid": session["id"]})).mappings().first()
        if not run or run["status"] != "running" or run["deadline_at"] > now:
            return
        event = {"type": "error", "message": "查询超过执行期限，请重新提问。"}
        await db.execute(text("UPDATE query_run SET status='interrupted', outcome='error', end_reason='deadline_exceeded', terminal_event=:event, finished_at=:now WHERE id=:id AND status='running'"), {"event": _json(event), "now": now, "id": rid})
        await db.execute(text("UPDATE chat_session SET active_run_id=NULL WHERE id=:sid AND active_run_id=:id"), {"sid": session["id"], "id": rid})
        session["active_run_id"] = None
        # 超时的问题也补进对话历史：否则下一轮改写根本不知道用户问过它，
        # "请重新提问"之后的多轮语境就断了
        context = _decode(session.get("context_json")) or []
        context.append({"type": "human", "content": run["raw_query"]})
        context.append({"type": "ai", "content": event["message"]})
        context = context[-_CONTEXT_LIMIT:]
        await db.execute(text("UPDATE chat_session SET context_json=:context WHERE id=:sid"), {"context": _json(context), "sid": session["id"]})
        session["context_json"] = _json(context)

    async def recover_on_startup(self):
        """Single-worker demonstration: interrupt runs abandoned by a former process."""
        async with self.factory() as db, db.begin():
            now = await self._now(db)
            notice = "服务重启，本次查询已中断，请重新提问。"
            event = _json({"type": "error", "message": notice})
            await db.execute(text("UPDATE query_run SET status='interrupted', outcome='error', end_reason='process_restart', terminal_event=:event, finished_at=:now WHERE status='running'"), {"event": event, "now": now})
            # 与超时同理：被丢弃的问题要进历史，重启后追问才有上下文
            rows = (await db.execute(text("SELECT cs.id, cs.context_json, qr.raw_query FROM chat_session cs JOIN query_run qr ON qr.id=cs.active_run_id WHERE cs.active_run_id IS NOT NULL"))).mappings().all()
            for row in rows:
                context = _decode(row["context_json"]) or []
                context.append({"type": "human", "content": row["raw_query"]})
                context.append({"type": "ai", "content": notice})
                context = context[-_CONTEXT_LIMIT:]
                await db.execute(text("UPDATE chat_session SET active_run_id=NULL, context_json=:context WHERE id=:id"), {"context": _json(context), "id": row["id"]})
            await db.execute(text("UPDATE chat_session SET active_run_id=NULL WHERE active_run_id IS NOT NULL"))

    async def create(self, owner: str, role: str, scope: str, title: str = "新会话") -> dict:
        sid = str(uuid.uuid4())
        async with self.factory() as db, db.begin():
            now = await self._now(db)
            await db.execute(text("INSERT INTO chat_session (id,owner_subject,role_snapshot,scope_hash,title,context_json,created_at,updated_at) VALUES (:id,:owner,:role,:scope,:title,:context,:now,:now)"), {"id": sid, "owner": owner.encode("utf-8"), "role": role, "scope": scope, "title": title, "context": "[]", "now": now})
        return {"id": sid, "title": title, "created_at": _stamp(now), "updated_at": _stamp(now), "scope_changed": False}

    async def list_sessions(self, owner: str, scope: str, limit: int = 20, cursor: tuple[datetime, str] | None = None) -> dict:
        params = {"owner": owner.encode("utf-8"), "scope": scope, "limit": limit + 1}
        where = " AND (updated_at < :at OR (updated_at = :at AND id < :id))" if cursor else ""
        if cursor:
            params.update(at=cursor[0], id=cursor[1])
        async with self.factory() as db:
            rows = (await db.execute(text("SELECT id,title,scope_hash,created_at,updated_at FROM chat_session WHERE owner_subject=:owner AND deleted_at IS NULL" + where + " ORDER BY updated_at DESC,id DESC LIMIT :limit"), params)).mappings().all()
        items = [{"id": r["id"], "title": r["title"] if r["scope_hash"] == scope else None, "scope_changed": r["scope_hash"] != scope, "created_at": _stamp(r["created_at"]), "updated_at": _stamp(r["updated_at"])} for r in rows[:limit]]
        last = rows[limit-1] if len(rows) > limit else None
        return {"items": items, "next_cursor": [last["updated_at"].isoformat(), last["id"]] if last else None}

    async def rename(self, sid: str, owner: str, scope: str, title: str) -> dict:
        async with self.factory() as db, db.begin():
            session = await self._session(db, sid, owner, scope, lock=True)
            now = await self._now(db)
            await db.execute(text("UPDATE chat_session SET title=:title,updated_at=:now WHERE id=:id"), {"title": title, "now": now, "id": sid})
        return {"id": sid, "title": title, "created_at": _stamp(session["created_at"]), "updated_at": _stamp(now), "scope_changed": False}

    async def delete(self, sid: str, owner: str, scope: str):
        async with self.factory() as db, db.begin():
            session = await self._session(db, sid, owner, scope, lock=True)
            now = await self._now(db)
            await self._expire(db, session, now)
            if session["active_run_id"]:
                raise HistoryError(409, "SESSION_BUSY")
            await db.execute(text("UPDATE chat_session SET deleted_at=:now,updated_at=:now WHERE id=:id"), {"now": now, "id": sid})

    async def prepare(self, sid: str, owner: str, role: str, scope: str, request_id: str, query: str) -> PreparedRun:
        async with self.factory() as db, db.begin():
            session = await self._session(db, sid, owner, scope, lock=True)
            now = await self._now(db)
            await self._expire(db, session, now)
            existing = (await db.execute(text("SELECT * FROM query_run WHERE session_id=:sid AND client_request_id=:key FOR UPDATE"), {"sid": sid, "key": request_id})).mappings().first()
            if existing:
                if existing["raw_query"] != query:
                    raise HistoryError(409, "IDEMPOTENCY_CONFLICT")
                if existing["status"] == "running":
                    raise HistoryError(409, "REQUEST_IN_PROGRESS", session_id=sid, client_request_id=request_id, run_id=existing["id"], status="running", deadline_at=_stamp(existing["deadline_at"]))
                return PreparedRun(existing["id"], sid, request_id, query, [], existing["deadline_at"], replay=run_view(existing))
            if session["active_run_id"]:
                raise HistoryError(409, "SESSION_BUSY", run_id=session["active_run_id"])
            rid = str(uuid.uuid4())
            deadline = (await db.execute(text("SELECT DATE_ADD(UTC_TIMESTAMP(6), INTERVAL :seconds SECOND)"), {"seconds": self.timeout_seconds})).scalar_one()
            await db.execute(text("INSERT INTO query_run (id,session_id,client_request_id,raw_query,status,role_snapshot,scope_hash,created_at,deadline_at) VALUES (:id,:sid,:key,:query,'running',:role,:scope,:now,:deadline)"), {"id": rid, "sid": sid, "key": request_id, "query": query, "role": role, "scope": scope, "now": now, "deadline": deadline})
            await db.execute(text("UPDATE chat_session SET active_run_id=:rid WHERE id=:sid"), {"rid": rid, "sid": sid})
            return PreparedRun(rid, sid, request_id, query, _decode(session["context_json"]) or [], deadline)

    async def finish(self, prepared: PreparedRun, owner: str, scope: str, status: str, outcome: str, event: dict, context: list[dict] | None = None, rewritten: str | None = None, end_reason: str | None = None) -> dict:
        async with self.factory() as db, db.begin():
            session = await self._session(db, prepared.session_id, owner, scope, lock=True)
            now = await self._now(db)
            await self._expire(db, session, now)
            run = (await db.execute(text("SELECT * FROM query_run WHERE id=:id AND session_id=:sid FOR UPDATE"), {"id": prepared.run_id, "sid": prepared.session_id})).mappings().one()
            if run["status"] != "running" or session["active_run_id"] != prepared.run_id or run["deadline_at"] <= now:
                return run_view(run)
            changed = await db.execute(text("UPDATE query_run SET status=:status,outcome=:outcome,terminal_event=:event,rewritten_query=:rewritten,finished_at=:now,end_reason=:reason WHERE id=:id AND status='running' AND deadline_at>UTC_TIMESTAMP(6)"), {"status": status, "outcome": outcome, "event": _json(event), "rewritten": rewritten, "now": now, "reason": end_reason, "id": prepared.run_id})
            if changed.rowcount != 1:
                return run_view(run)
            await db.execute(text("UPDATE chat_session SET active_run_id=NULL,context_json=COALESCE(:context,context_json),updated_at=:now,title=CASE WHEN title='新会话' THEN :title ELSE title END WHERE id=:sid AND active_run_id=:rid"), {"context": _json(context) if context is not None else None, "now": now, "title": prepared.raw_query[:30], "sid": prepared.session_id, "rid": prepared.run_id})
            result = dict(run)
            result.update(status=status, outcome=outcome, terminal_event=event, rewritten_query=rewritten, finished_at=now, end_reason=end_reason)
            return run_view(result)

    async def get_run(self, sid: str, owner: str, scope: str, *, rid: str | None = None, request_id: str | None = None) -> dict:
        async with self.factory() as db, db.begin():
            session = await self._session(db, sid, owner, scope, lock=True)
            await self._expire(db, session, await self._now(db))
            field, value = ("id", rid) if rid else ("client_request_id", request_id)
            run = (await db.execute(text(f"SELECT * FROM query_run WHERE session_id=:sid AND {field}=:value"), {"sid": sid, "value": value})).mappings().first()
            if not run:
                raise HistoryError(404, "RUN_NOT_FOUND" if rid else "REQUEST_NOT_REGISTERED")
            return run_view(run)

    async def list_runs(self, sid: str, owner: str, scope: str, limit: int = 20, cursor: tuple[datetime, str] | None = None) -> dict:
        async with self.factory() as db, db.begin():
            session = await self._session(db, sid, owner, scope, lock=True)
            await self._expire(db, session, await self._now(db))
            params = {"sid": sid, "limit": limit + 1}
            where = " AND (created_at < :at OR (created_at = :at AND id < :id))" if cursor else ""
            if cursor:
                params.update(at=cursor[0], id=cursor[1])
            rows = (await db.execute(text("SELECT * FROM query_run WHERE session_id=:sid" + where + " ORDER BY created_at DESC,id DESC LIMIT :limit"), params)).mappings().all()
            items = [run_view(r) for r in reversed(rows[:limit])]
            last = rows[limit-1] if len(rows) > limit else None
            return {"session": {"id": sid, "title": session["title"]}, "items": items, "next_cursor": [last["created_at"].isoformat(), last["id"]] if last else None}
