"""File and folder management API routes.

All file mutations (save, delete, restore) go through Change Requests.
Only CR merge writes to the live S3 path. Folder ops remain direct.
"""

import difflib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, Form
from pydantic import BaseModel
from sqlalchemy import select, func, desc, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user, require_role
from app.database import get_db
from app.models import (
    User, FileVersion, AuditLog, UserRole,
    ChangeRequest, ChangeRequestFile, CRStatus, FileAction,
    FileShare,
)
from app.s3 import S3Service
from app.config import get_settings

router = APIRouter(prefix="/api/files", tags=["files"])
settings = get_settings()

s3 = S3Service()

# Roles that can write
WRITE_ROLES = (UserRole.admin.value, UserRole.approver.value, UserRole.editor.value)


# ── Schemas ──────────────────────────────────────────────────────────────

class FileInfo(BaseModel):
    path: str
    name: str
    is_folder: bool
    size: int = 0
    version: int = 0
    last_modified: Optional[str] = None
    author: Optional[str] = None
    author_id: Optional[int] = None
    is_public: bool = False
    is_archived: bool = False


class FileDetail(BaseModel):
    path: str
    content: Optional[str] = None
    size: int = 0
    version: int = 0
    content_hash: str = ""
    author: Optional[str] = None
    author_id: Optional[int] = None
    message: str = ""
    is_binary: bool = False
    created_at: Optional[str] = None


class VersionInfo(BaseModel):
    id: int
    version: int
    size: int
    content_hash: str
    author: str
    message: str
    is_delete: bool
    created_at: str


class DiffResponse(BaseModel):
    file_path: str
    old_version: int
    new_version: int
    diff_html: str
    old_content: str
    new_content: str


class SaveFileRequest(BaseModel):
    path: str
    content: str
    message: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_latest_version(db: AsyncSession, file_path: str) -> Optional[FileVersion]:
    result = await db.execute(
        select(FileVersion)
        .options(selectinload(FileVersion.author))
        .where(FileVersion.file_path == file_path)
        .order_by(desc(FileVersion.version))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _get_next_version(db: AsyncSession, file_path: str) -> int:
    result = await db.execute(
        select(func.max(FileVersion.version)).where(FileVersion.file_path == file_path)
    )
    current = result.scalar()
    return (current or 0) + 1


def _generate_diff_html(old_lines: list[str], new_lines: list[str], old_label: str, new_label: str) -> str:
    diff = difflib.HtmlDiff(tabsize=4, wrapcolumn=120)
    return diff.make_table(old_lines, new_lines, fromdesc=old_label, todesc=new_label, context=True, numlines=5)


async def _get_or_create_draft_cr(db: AsyncSession, user: User, request: Request) -> ChangeRequest:
    """Find the user's most recent draft CR, or create one.

    This consolidates all file changes into a single working draft per user,
    keeping the CR count low.
    """
    result = await db.execute(
        select(ChangeRequest)
        .options(selectinload(ChangeRequest.files))
        .where(
            ChangeRequest.author_id == user.id,
            ChangeRequest.status == CRStatus.draft.value,
        )
        .order_by(desc(ChangeRequest.updated_at))
        .limit(1)
    )
    cr = result.scalar_one_or_none()
    if cr:
        return cr

    # Create a new draft CR
    cr = ChangeRequest(
        title=f"Changes by {user.username}",
        description="Auto-created draft. Edit the title before submitting.",
        author_id=user.id,
        status=CRStatus.draft.value,
    )
    db.add(cr)
    await db.flush()
    # Note: do not access or assign relationship attributes here to avoid
    # triggering lazy loads in the async context. The relationship will be
    # empty for a new instance.

    db.add(AuditLog(
        user_id=user.id, action="cr.auto_create", resource_type="change_request",
        resource_id=str(cr.id), ip_address=request.client.host if request.client else "",
    ))
    return cr


async def _stage_file_in_cr(
    db: AsyncSession, cr: ChangeRequest, file_path: str,
    action: str, content_bytes: Optional[bytes] = None,
) -> ChangeRequestFile:
    """Add or replace a file entry in a draft CR."""
    # Remove existing entry for this path. Query the CR files directly to
    # avoid triggering a lazy-load / autoflush on `cr.files` in the async
    # context which causes MissingGreenlet errors.
    result = await db.execute(
        select(ChangeRequestFile).where(
            ChangeRequestFile.change_request_id == cr.id,
            ChangeRequestFile.file_path == file_path,
        )
    )
    existing = result.scalars().all()
    for e in existing:
        if e.staging_s3_key:
            try:
                await s3.delete_object(e.staging_s3_key)
            except Exception:
                pass
        await db.delete(e)

    staging_key = None
    base_version_id = None

    # Upload staged content for create/edit
    if action in (FileAction.create.value, FileAction.edit.value) and content_bytes is not None:
        staging_key = S3Service.generate_staging_key()
        await s3.put_object(staging_key, content_bytes, S3Service.guess_content_type(file_path))

    # Find base version for edit/delete
    if action in (FileAction.edit.value, FileAction.delete.value):
        latest_result = await db.execute(
            select(FileVersion)
            .where(FileVersion.file_path == file_path)
            .order_by(desc(FileVersion.version))
            .limit(1)
        )
        latest = latest_result.scalar_one_or_none()
        if latest and not latest.is_delete:
            base_version_id = latest.id

    crf = ChangeRequestFile(
        change_request_id=cr.id,
        file_path=file_path,
        action=action,
        staging_s3_key=staging_key,
        base_version_id=base_version_id,
    )
    db.add(crf)
    # Do not append to `cr.files` here; mutating the relationship can trigger a
    # lazy-load/autoflush on `cr` in the async context which leads to
    # MissingGreenlet errors. The `change_request_id` is already set on `crf`.
    await db.flush()
    return crf


# ── Browse ───────────────────────────────────────────────────────────────

@router.get("/browse")
async def browse(
    path: str = Query("", description="Directory path to browse"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List files and folders at a given path."""
    prefix = path.strip("/")
    if prefix:
        prefix += "/"

    result = await s3.list_objects(prefix=prefix, delimiter="/")

    items = []
    file_paths = []
    for folder in sorted(result["folders"]):
        name = folder.rstrip("/").rsplit("/", 1)[-1] if "/" in folder.rstrip("/") else folder.rstrip("/")
        if name.startswith("_"):
            continue
        items.append(FileInfo(path=folder.rstrip("/"), name=name, is_folder=True))

    for f in sorted(result["files"], key=lambda x: x["key"]):
        key = f["key"]
        if key == prefix or key.startswith("_"):
            continue
        name = key.rsplit("/", 1)[-1] if "/" in key else key
        if not name:
            continue
        file_paths.append(key)
        items.append(FileInfo(
            path=key, name=name, is_folder=False,
            size=f["size"],
            last_modified=f["last_modified"].isoformat() if f.get("last_modified") else None,
        ))

    # Enrich file items with share/archive state
    if file_paths:
        shares_result = await db.execute(
            select(FileShare).where(FileShare.file_path.in_(file_paths))
        )
        share_map = {s.file_path: s for s in shares_result.scalars().all()}
        for item in items:
            if not item.is_folder and item.path in share_map:
                share = share_map[item.path]
                item.is_public = share.is_public
                item.is_archived = share.is_archived

        # Fetch latest version info (including author) for each file
        ver_subq = (
            select(FileVersion.file_path, func.max(FileVersion.version).label('max_ver'))
            .where(FileVersion.file_path.in_(file_paths))
            .group_by(FileVersion.file_path)
            .subquery()
        )
        ver_res = await db.execute(
            select(FileVersion)
            .join(ver_subq, and_(FileVersion.file_path == ver_subq.c.file_path, FileVersion.version == ver_subq.c.max_ver))
            .options(selectinload(FileVersion.author))
        )
        latest_map = {v.file_path: v for v in ver_res.scalars().all()}
        for item in items:
            if not item.is_folder and item.path in latest_map:
                v = latest_map[item.path]
                item.version = v.version
                item.author = v.author.username if v.author else None
                item.author_id = v.author_id if hasattr(v, 'author_id') else (v.author.id if v.author else None)

    return {"path": prefix.rstrip("/"), "items": items}


# ── Read file ────────────────────────────────────────────────────────────

@router.get("/content")
async def get_file_content(
    path: str, version: Optional[int] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get file content, optionally at a specific version."""
    if version:
        result = await db.execute(
            select(FileVersion).options(selectinload(FileVersion.author))
            .where(FileVersion.file_path == path, FileVersion.version == version)
        )
        fv = result.scalar_one_or_none()
        if not fv:
            raise HTTPException(404, f"Version {version} not found for {path}")
        if fv.is_delete:
            raise HTTPException(410, "This version is a deletion record")
        try:
            content_bytes = await s3.get_object(fv.s3_key)
        except Exception:
            raise HTTPException(404, "Version content not found in storage")
    else:
        fv = await _get_latest_version(db, path)
        if fv and fv.is_delete:
            raise HTTPException(410, "File has been deleted")
        try:
            content_bytes = await s3.get_object(path)
        except Exception:
            raise HTTPException(404, "File not found")

    is_binary = not S3Service.is_text_file(path)
    content = None if is_binary else content_bytes.decode("utf-8", errors="replace")

    return FileDetail(
        path=path,
        content=content,
        size=len(content_bytes),
        version=fv.version if fv else 0,
        content_hash=fv.content_hash if fv else S3Service.compute_hash(content_bytes),
        author=fv.author.username if fv and fv.author else None,
        author_id=fv.author_id if (fv and hasattr(fv, 'author_id')) else (fv.author.id if fv and fv.author else None),
        message=fv.message if fv else "",
        is_binary=is_binary,
        created_at=fv.created_at.isoformat() if fv else None,
    )


# ── Save file (stages in CR) ────────────────────────────────────────────

@router.post("/save")
async def save_file(
    req: SaveFileRequest,
    request: Request,
    user: User = Depends(require_role(*WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Save a file by staging it in the user's draft Change Request."""
    path = req.path.strip("/")
    if not path:
        raise HTTPException(400, "Path is required")
    if path.startswith("_"):
        raise HTTPException(400, "Paths starting with _ are reserved")

    content_bytes = req.content.encode("utf-8")

    # Determine action: create vs edit
    latest = await _get_latest_version(db, path)
    is_new = not latest or latest.is_delete
    action = FileAction.create.value if is_new else FileAction.edit.value

    # Find or create draft CR
    cr = await _get_or_create_draft_cr(db, user, request)

    # Update CR title if it's the auto-generated one and user provided a message
    if req.message and cr.title.startswith("Changes by "):
        cr.title = req.message

    # Stage the file
    await _stage_file_in_cr(db, cr, path, action, content_bytes)

    db.add(AuditLog(
        user_id=user.id, action=f"file.stage_{action}", resource_type="file",
        resource_id=path, details={"cr_id": cr.id},
        ip_address=request.client.host if request.client else "",
    ))
    await db.flush()

    # Refresh to get the relationship without triggering lazy load
    await db.refresh(cr, ["files"])

    return {
        "cr_id": cr.id,
        "cr_title": cr.title,
        "path": path,
        "action": action,
        "file_count": len(cr.files),
    }


# ── Upload binary file (stages in CR) ───────────────────────────────────

@router.post("/upload")
async def upload_file(
    request: Request,
    path: str = Form(...),
    message: str = Form(""),
    file: UploadFile = File(...),
    user: User = Depends(require_role(*WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Upload a binary file by staging it in the user's draft CR."""
    path = path.strip("/")
    if not path:
        raise HTTPException(400, "Path is required")
    if path.startswith("_"):
        raise HTTPException(400, "Paths starting with _ are reserved")

    content_bytes = await file.read()
    max_size = settings.max_file_size_mb * 1024 * 1024
    if len(content_bytes) > max_size:
        raise HTTPException(413, f"File exceeds {settings.max_file_size_mb}MB limit")

    latest = await _get_latest_version(db, path)
    is_new = not latest or latest.is_delete
    action = FileAction.create.value if is_new else FileAction.edit.value

    cr = await _get_or_create_draft_cr(db, user, request)
    if message and cr.title.startswith("Changes by "):
        cr.title = message

    await _stage_file_in_cr(db, cr, path, action, content_bytes)

    return {"cr_id": cr.id, "path": path, "action": action}


# ── Delete file (stages in CR) ──────────────────────────────────────────

@router.delete("/delete")
async def delete_file(
    path: str, request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a file by staging a deletion in the user's draft CR."""
    path = path.strip("/")
    latest = await _get_latest_version(db, path)
    if not latest or latest.is_delete:
        raise HTTPException(404, "File not found or already deleted")

    # Permission: allow if user has a write role, or is the author of the latest version
    if user.role not in WRITE_ROLES and (not latest.author or latest.author_id != user.id):
        raise HTTPException(403, "Insufficient permissions to delete this file")

    cr = await _get_or_create_draft_cr(db, user, request)
    await _stage_file_in_cr(db, cr, path, FileAction.delete.value)

    db.add(AuditLog(
        user_id=user.id, action="file.stage_delete", resource_type="file",
        resource_id=path, details={"cr_id": cr.id},
        ip_address=request.client.host if request.client else "",
    ))
    await db.flush()

    # Refresh to get the relationship without triggering lazy load
    await db.refresh(cr, ["files"])
    return {"cr_id": cr.id, "path": path, "action": "delete", "file_count": len(cr.files)}


# ── Create folder (direct, no CR needed) ────────────────────────────────

@router.post("/folder")
async def create_folder(
    path: str = Query(...), request: Request = None,
    user: User = Depends(require_role(*WRITE_ROLES)),
):
    """Create a folder (empty object with trailing slash in S3)."""
    path = path.strip("/")
    if not path:
        raise HTTPException(400, "Path is required")
    if path.startswith("_"):
        raise HTTPException(400, "Paths starting with _ are reserved")
    await s3.put_object(path + "/", b"", "application/x-directory")
    return {"ok": True, "path": path}


# ── Delete folder (stages each file delete in CR) ───────────────────────

@router.delete("/folder")
async def delete_folder(
    path: str = Query(...), request: Request = None,
    user: User = Depends(require_role(*WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Delete a folder - stages delete for each tracked file in user's draft CR."""
    path = path.strip("/")
    if not path:
        raise HTTPException(400, "Cannot delete root")

    result = await s3.list_objects(prefix=path + "/")
    keys = [f["key"] for f in result["files"]]
    if not keys:
        raise HTTPException(404, "Folder not found or empty")

    cr = await _get_or_create_draft_cr(db, user, request)
    staged = 0

    for key in keys:
        if key.startswith("_"):
            continue
        latest = await _get_latest_version(db, key)
        if latest and not latest.is_delete:
            await _stage_file_in_cr(db, cr, key, FileAction.delete.value)
            staged += 1

    # Delete the folder marker itself (it's just an S3 convention)
    folder_marker = path + "/"
    try:
        await s3.delete_object(folder_marker)
    except Exception:
        pass

    return {"cr_id": cr.id, "path": path, "staged_deletes": staged}


# ── Version history ──────────────────────────────────────────────────────

@router.get("/history")
async def file_history(
    path: str, user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get version history for a file."""
    result = await db.execute(
        select(FileVersion).options(selectinload(FileVersion.author))
        .where(FileVersion.file_path == path)
        .order_by(desc(FileVersion.version))
    )
    versions = result.scalars().all()
    if not versions:
        raise HTTPException(404, "No history found for this file")

    return [
        VersionInfo(
            id=v.id, version=v.version, size=v.size,
            content_hash=v.content_hash, author=v.author.username,
            message=v.message, is_delete=v.is_delete,
            created_at=v.created_at.isoformat(),
        )
        for v in versions
    ]


# ── Restore version (stages in CR) ──────────────────────────────────────

@router.post("/restore")
async def restore_version(
    path: str, version: int, request: Request,
    user: User = Depends(require_role(*WRITE_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    """Restore a file to a specific version by staging it in the user's draft CR."""
    result = await db.execute(
        select(FileVersion).where(
            FileVersion.file_path == path, FileVersion.version == version
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Version not found")
    if target.is_delete:
        raise HTTPException(400, "Cannot restore a deletion version")

    content = await s3.get_object(target.s3_key)

    cr = await _get_or_create_draft_cr(db, user, request)
    await _stage_file_in_cr(db, cr, path, FileAction.edit.value, content)

    db.add(AuditLog(
        user_id=user.id, action="file.stage_restore", resource_type="file",
        resource_id=path, details={"cr_id": cr.id, "from_version": version},
        ip_address=request.client.host if request.client else "",
    ))

    return {"cr_id": cr.id, "path": path, "restored_version": version}


# ── Diff ─────────────────────────────────────────────────────────────────

@router.get("/diff")
async def diff_versions(
    path: str, old: int, new: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Compare two versions of a file."""
    result_old = await db.execute(
        select(FileVersion).where(FileVersion.file_path == path, FileVersion.version == old)
    )
    result_new = await db.execute(
        select(FileVersion).where(FileVersion.file_path == path, FileVersion.version == new)
    )
    ver_old = result_old.scalar_one_or_none()
    ver_new = result_new.scalar_one_or_none()
    if not ver_old or not ver_new:
        raise HTTPException(404, "One or both versions not found")

    old_content = ""
    if not ver_old.is_delete:
        try:
            old_bytes = await s3.get_object(ver_old.s3_key)
            old_content = old_bytes.decode("utf-8", errors="replace")
        except Exception:
            old_content = "[Content unavailable]"

    new_content = ""
    if not ver_new.is_delete:
        try:
            new_bytes = await s3.get_object(ver_new.s3_key)
            new_content = new_bytes.decode("utf-8", errors="replace")
        except Exception:
            new_content = "[Content unavailable]"

    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()
    diff_html = _generate_diff_html(old_lines, new_lines, f"v{old}", f"v{new}")

    return DiffResponse(
        file_path=path, old_version=old, new_version=new,
        diff_html=diff_html, old_content=old_content, new_content=new_content,
    )


# ── Search ───────────────────────────────────────────────────────────────

@router.get("/search")
async def search_files(
    q: str = Query(..., min_length=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search files by path name."""
    subq = (
        select(FileVersion.file_path, func.max(FileVersion.version).label("max_ver"))
        .group_by(FileVersion.file_path)
        .subquery()
    )
    result = await db.execute(
        select(FileVersion)
        .join(subq, and_(
            FileVersion.file_path == subq.c.file_path,
            FileVersion.version == subq.c.max_ver,
        ))
        .where(FileVersion.is_delete == False, FileVersion.file_path.ilike(f"%{q}%"))
        .order_by(FileVersion.file_path)
        .limit(50)
    )
    versions = result.scalars().all()
    return [{"path": v.file_path, "size": v.size, "version": v.version} for v in versions]
