"""File sharing, public links, and archive API routes."""

import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, require_role
from app.database import get_db
from app.models import User, FileShare, AuditLog, UserRole

router = APIRouter(prefix="/api/files", tags=["sharing"])

WRITE_ROLES = (UserRole.admin.value, UserRole.approver.value, UserRole.editor.value)


async def _get_or_create_share(db: AsyncSession, file_path: str, user: User) -> FileShare:
    """Find or create a FileShare record for the given path."""
    result = await db.execute(
        select(FileShare).where(FileShare.file_path == file_path)
    )
    share = result.scalar_one_or_none()
    if share:
        return share

    share = FileShare(
        file_path=file_path,
        token=uuid.uuid4().hex,
        is_public=False,
        is_archived=False,
        created_by_id=user.id,
    )
    db.add(share)
    await db.flush()
    return share


@router.get("/share-info")
async def share_info(
    path: str = Query(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get sharing/archive state for a file."""
    result = await db.execute(
        select(FileShare).where(FileShare.file_path == path)
    )
    share = result.scalar_one_or_none()
    if not share:
        return {
            "is_public": False,
            "is_archived": False,
            "token": None,
            "public_url": None,
        }
    return {
        "is_public": share.is_public,
        "is_archived": share.is_archived,
        "token": share.token,
        # public raw URL is path-based (encode segments so slashes remain separators)
        "public_url": ("/public/raw/" + "/".join([quote(p) for p in share.file_path.split("/")])) if share.is_public else None,
    }


@router.post("/toggle-public")
async def toggle_public(
    path: str = Query(...),
    request: Request = None,
    user: User = Depends(require_role(*WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Toggle public link for a file."""
    share = await _get_or_create_share(db, path, user)
    share.is_public = not share.is_public

    db.add(AuditLog(
        user_id=user.id,
        action="file.toggle_public",
        resource_type="file",
        resource_id=path,
        details={"is_public": share.is_public, "token": share.token},
        ip_address=request.client.host if request and request.client else "",
    ))

    return {
        "is_public": share.is_public,
        "token": share.token,
        "public_url": ("/public/raw/" + "/".join([quote(p) for p in share.file_path.split("/")])) if share.is_public else None,
    }


@router.post("/toggle-archive")
async def toggle_archive(
    path: str = Query(...),
    request: Request = None,
    user: User = Depends(require_role(*WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Toggle archive state for a file."""
    share = await _get_or_create_share(db, path, user)
    share.is_archived = not share.is_archived

    db.add(AuditLog(
        user_id=user.id,
        action="file.toggle_archive",
        resource_type="file",
        resource_id=path,
        details={"is_archived": share.is_archived},
        ip_address=request.client.host if request and request.client else "",
    ))

    return {
        "is_archived": share.is_archived,
    }
