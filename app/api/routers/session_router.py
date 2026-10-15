"""Authenticated, owner-scoped session and run APIs."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from starlette.responses import Response

from app.api.dependencies import get_current_identity, get_session_service
from app.api.schemas.session_schema import SessionCreate, SessionRename
from app.core.security import Identity
from app.services.export_service import export_csv
from app.services.session_service import SessionService

router = APIRouter(prefix="/api/sessions", tags=["sessions"])
CurrentUser = Annotated[Identity, Depends(get_current_identity)]
Sessions = Annotated[SessionService, Depends(get_session_service)]


@router.post("")
async def create_session(body: SessionCreate, identity: CurrentUser, service: Sessions):
    return await service.create(identity, body.title)


@router.get("")
async def list_sessions(identity: CurrentUser, service: Sessions, limit: int = Query(20, ge=1, le=100), cursor: str | None = None):
    return await service.list(identity, limit, cursor)


@router.get("/{sid}/runs/by-request/{key}")
async def get_by_request(sid: UUID, key: UUID, identity: CurrentUser, service: Sessions):
    return await service.by_request(identity, str(sid), str(key))


@router.get("/{sid}/runs/{rid}/export")
async def download_csv(sid: UUID, rid: UUID, identity: CurrentUser, service: Sessions, format: str = "csv"):
    from app.repositories.mysql.meta.history_repository import HistoryError
    if format != "csv":
        raise HistoryError(422, "UNSUPPORTED_EXPORT_FORMAT")
    run = await service.run(identity, str(sid), str(rid))
    contents = export_csv(run)
    return Response(content=contents, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="query-{rid}.csv"', "Cache-Control": "no-store"})


@router.get("/{sid}/runs/{rid}")
async def get_run(sid: UUID, rid: UUID, identity: CurrentUser, service: Sessions):
    return await service.run(identity, str(sid), str(rid))


@router.get("/{sid}/runs")
async def list_runs(sid: UUID, identity: CurrentUser, service: Sessions, limit: int = Query(20, ge=1, le=100), cursor: str | None = None):
    return await service.runs(identity, str(sid), limit, cursor)


@router.patch("/{sid}")
async def rename_session(sid: UUID, body: SessionRename, identity: CurrentUser, service: Sessions):
    return await service.rename(identity, str(sid), body.title)


@router.delete("/{sid}", status_code=204)
async def delete_session(sid: UUID, identity: CurrentUser, service: Sessions):
    await service.delete(identity, str(sid))
    return Response(status_code=204)
