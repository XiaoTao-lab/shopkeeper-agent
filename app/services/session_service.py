"""User-scoped session policy and stable opaque pagination cursors."""
import base64
import hashlib
import json
from datetime import datetime

from app.conf.app_config import app_config
from app.core.security import Identity
from app.repositories.mysql.meta.history_repository import (
    HistoryError,
    HistoryRepository,
)

POLICY_VERSION = 1  # Bump whenever row/column permission semantics change.


def scope_hash(role: str) -> str:
    config = app_config.rbac.roles[role]
    canonical = json.dumps({"version": POLICY_VERSION, "role": role, "regions": sorted(set(config.region_ids)), "masked": sorted(set(config.mask_columns))}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def encode_cursor(value: list[str] | None) -> str | None:
    if not value:
        return None
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        at, key = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        return datetime.fromisoformat(at), str(key)
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        raise HistoryError(422, "INVALID_CURSOR") from exc


class SessionService:
    def __init__(self, history: HistoryRepository):
        self.history = history

    def _scope(self, identity: Identity) -> str:
        return scope_hash(identity.role)

    async def create(self, identity: Identity, title: str = "新会话"):
        return await self.history.create(identity.subject, identity.role, self._scope(identity), title)

    async def list(self, identity: Identity, limit: int, cursor: str | None):
        result = await self.history.list_sessions(identity.subject, self._scope(identity), limit, decode_cursor(cursor))
        result["next_cursor"] = encode_cursor(result["next_cursor"])
        return result

    async def rename(self, identity: Identity, sid: str, title: str):
        return await self.history.rename(sid, identity.subject, self._scope(identity), title)

    async def delete(self, identity: Identity, sid: str):
        return await self.history.delete(sid, identity.subject, self._scope(identity))

    async def prepare(self, identity: Identity, sid: str, key: str, query: str):
        return await self.history.prepare(sid, identity.subject, identity.role, self._scope(identity), key, query)

    async def run(self, identity: Identity, sid: str, rid: str):
        return await self.history.get_run(sid, identity.subject, self._scope(identity), rid=rid)

    async def by_request(self, identity: Identity, sid: str, key: str):
        return await self.history.get_run(sid, identity.subject, self._scope(identity), request_id=key)

    async def runs(self, identity: Identity, sid: str, limit: int, cursor: str | None):
        result = await self.history.list_runs(sid, identity.subject, self._scope(identity), limit, decode_cursor(cursor))
        result["next_cursor"] = encode_cursor(result["next_cursor"])
        return result
