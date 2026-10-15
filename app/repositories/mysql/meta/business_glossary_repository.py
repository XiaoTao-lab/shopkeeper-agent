"""Short-transaction persistence for globally shared business terminology."""

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker


class GlossaryError(Exception):
    def __init__(self, http_status: int, code: str):
        self.status = http_status
        self.code = code
        super().__init__(code)


def _decode_aliases(value: Any) -> list[str]:
    decoded = json.loads(value) if isinstance(value, str) else value
    return [str(alias) for alias in decoded or []]


def _text(value: Any) -> str:
    return bytes(value).decode("utf-8") if isinstance(value, (bytes, bytearray)) else str(value)


def glossary_view(row: dict) -> dict:
    return {
        "id": row["id"],
        "term": row["term"],
        "definition": row["definition"],
        "aliases": _decode_aliases(row["aliases"]),
        "guidance": row["guidance"],
        "enabled": bool(row["enabled"]),
        "updated_by": _text(row["updated_by"]),
    }


class BusinessGlossaryRepository:
    def __init__(self, factory: async_sessionmaker):
        self.factory = factory

    async def list(self, *, enabled_only: bool) -> list[dict]:
        where = "WHERE enabled=TRUE" if enabled_only else ""
        async with self.factory() as db:
            rows = (
                await db.execute(
                    text(
                        "SELECT id,term,definition,aliases,guidance,enabled,updated_by "
                        f"FROM business_glossary {where} ORDER BY term"
                    )
                )
            ).mappings().all()
        return [glossary_view(dict(row)) for row in rows]

    async def create(
        self,
        *,
        term: str,
        definition: str,
        aliases: list[str],
        guidance: str,
        actor: str,
    ) -> dict:
        identifier = str(uuid.uuid4())
        async with self.factory() as db, db.begin():
            now = (await db.execute(text("SELECT UTC_TIMESTAMP(6)"))).scalar_one()
            try:
                await db.execute(
                    text(
                        "INSERT INTO business_glossary "
                        "(id,term,definition,aliases,guidance,enabled,created_by,updated_by,created_at,updated_at) "
                        "VALUES (:id,:term,:definition,:aliases,:guidance,TRUE,:actor,:actor,:now,:now)"
                    ),
                    {
                        "id": identifier,
                        "term": term,
                        "definition": definition,
                        "aliases": json.dumps(aliases, ensure_ascii=False),
                        "guidance": guidance,
                        "actor": actor.encode("utf-8"),
                        "now": now,
                    },
                )
            except IntegrityError as exc:
                raise GlossaryError(409, "GLOSSARY_TERM_EXISTS") from exc
        return {
            "id": identifier,
            "term": term,
            "definition": definition,
            "aliases": aliases,
            "guidance": guidance,
            "enabled": True,
            "updated_by": actor,
        }

    async def update(
        self,
        *,
        id: str,
        term: str,
        definition: str,
        aliases: list[str],
        guidance: str,
        enabled: bool,
        actor: str,
    ) -> dict:
        """按主键更新术语；term 有唯一约束，撞名时抛 409。"""
        async with self.factory() as db, db.begin():
            now = (await db.execute(text("SELECT UTC_TIMESTAMP(6)"))).scalar_one()
            try:
                result = await db.execute(
                    text(
                        "UPDATE business_glossary "
                        "SET term=:term, definition=:definition, aliases=:aliases, "
                        "guidance=:guidance, enabled=:enabled, updated_by=:actor, updated_at=:now "
                        "WHERE id=:id"
                    ),
                    {
                        "id": id,
                        "term": term,
                        "definition": definition,
                        "aliases": json.dumps(aliases, ensure_ascii=False),
                        "guidance": guidance,
                        "enabled": enabled,
                        "actor": actor.encode("utf-8"),
                        "now": now,
                    },
                )
            except IntegrityError as exc:
                raise GlossaryError(409, "GLOSSARY_TERM_EXISTS") from exc
            if result.rowcount == 0:
                raise GlossaryError(404, "GLOSSARY_TERM_NOT_FOUND")
        return {
            "id": id,
            "term": term,
            "definition": definition,
            "aliases": aliases,
            "guidance": guidance,
            "enabled": enabled,
            "updated_by": actor,
        }

    async def delete(self, *, id: str) -> bool:
        """按主键删除术语，返回是否真的删除了记录。"""
        async with self.factory() as db, db.begin():
            result = await db.execute(
                text("DELETE FROM business_glossary WHERE id=:id"), {"id": id}
            )
            return result.rowcount > 0
