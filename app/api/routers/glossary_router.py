"""业务字典管理接口（仅管理员可写）。

普通用户无权查看/修改术语：术语口径属于全局语义资产，
只能由管理员维护，避免任意账号把口径改乱后污染所有人的问数结果。
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from app.api.dependencies import get_business_glossary_repository, get_current_identity
from app.api.schemas.glossary_schema import GlossaryCreate, GlossaryUpdate
from app.core.security import Identity
from app.repositories.mysql.meta.business_glossary_repository import (
    BusinessGlossaryRepository,
    GlossaryError,
)

router = APIRouter(prefix="/api/glossary", tags=["glossary"])
CurrentUser = Annotated[Identity, Depends(get_current_identity)]
GlossaryRepo = Annotated[
    BusinessGlossaryRepository, Depends(get_business_glossary_repository)
]


def _require_admin(identity: Identity) -> None:
    if identity.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限"
        )


@router.get("")
async def list_terms(identity: CurrentUser, repo: GlossaryRepo):
    _require_admin(identity)
    return await repo.list(enabled_only=False)


@router.post("", status_code=201)
async def create_term(body: GlossaryCreate, identity: CurrentUser, repo: GlossaryRepo):
    _require_admin(identity)
    try:
        return await repo.create(
            term=body.term,
            definition=body.definition,
            aliases=body.aliases,
            guidance=body.guidance,
            actor=identity.subject,
        )
    except GlossaryError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from exc


@router.patch("/{term_id}")
async def update_term(
    term_id: str, body: GlossaryUpdate, identity: CurrentUser, repo: GlossaryRepo
):
    _require_admin(identity)
    try:
        return await repo.update(
            id=term_id,
            term=body.term,
            definition=body.definition,
            aliases=body.aliases,
            guidance=body.guidance,
            enabled=body.enabled,
            actor=identity.subject,
        )
    except GlossaryError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from exc


@router.delete("/{term_id}", status_code=204)
async def delete_term(term_id: str, identity: CurrentUser, repo: GlossaryRepo):
    _require_admin(identity)
    deleted = await repo.delete(id=term_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "GLOSSARY_TERM_NOT_FOUND"},
        )
    return Response(status_code=204)
