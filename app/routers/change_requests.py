"""Change Request (approval workflow) API routes."""

import difflib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user, require_role
from app.database import get_db
from app.models import (
    User, UserRole, ChangeRequest, ChangeRequestFile, CRStatus,
    FileAction, FileVersion, AuditLog,
)
from app.s3 import S3Service

router = APIRouter(prefix="/api/cr", tags=["change_requests"])
s3 = S3Service()


# ── Schemas ──────────────────────────────────────────────────────────────

class CreateCRRequest(BaseModel):
    title: str
    description: str = ""


class UpdateCRRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None


class AddFileRequest(BaseModel):
    file_path: str
    action: str  # create, edit, delete
    content: Optional[str] = None  # for create/edit


class ReviewRequest(BaseModel):
    action: str  # approve or reject
    comment: str = ""


class CRSummary(BaseModel):
    id: int
    title: str
    status: str
    author: str
    reviewer: Optional[str] = None
    file_count: int = 0
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class CRFileInfo(BaseModel):
    id: int
    file_path: str
    action: str
    base_version: Optional[int] = None


# ── List CRs ─────────────────────────────────────────────────────────────

@router.get("/list")
async def list_change_requests(
    status: Optional[str] = None,
    author_id: Optional[int] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(ChangeRequest)
        .options(selectinload(ChangeRequest.author), selectinload(ChangeRequest.reviewer),
                 selectinload(ChangeRequest.files))
    )
    if status:
        query = query.where(ChangeRequest.status == status)
    if author_id:
        query = query.where(ChangeRequest.author_id == author_id)
    query = query.order_by(desc(ChangeRequest.updated_at))
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    crs = result.scalars().all()

    # Total count
    count_q = select(func.count(ChangeRequest.id))
    if status:
        count_q = count_q.where(ChangeRequest.status == status)
    if author_id:
        count_q = count_q.where(ChangeRequest.author_id == author_id)
    total = (await db.execute(count_q)).scalar()

    return {
        "items": [
            CRSummary(
                id=cr.id, title=cr.title, status=cr.status,
                author=cr.author.username,
                reviewer=cr.reviewer.username if cr.reviewer else None,
                file_count=len(cr.files),
                created_at=cr.created_at.isoformat(),
                updated_at=cr.updated_at.isoformat(),
            )
            for cr in crs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ── Get CR detail ────────────────────────────────────────────────────────

@router.get("/{cr_id}")
async def get_change_request(
    cr_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChangeRequest)
        .options(
            selectinload(ChangeRequest.author),
            selectinload(ChangeRequest.reviewer),
            selectinload(ChangeRequest.files).selectinload(ChangeRequestFile.base_version),
        )
        .where(ChangeRequest.id == cr_id)
    )
    cr = result.scalar_one_or_none()
    if not cr:
        raise HTTPException(404, "Change request not found")

    files = [
        CRFileInfo(
            id=f.id, file_path=f.file_path, action=f.action,
            base_version=f.base_version.version if f.base_version else None,
        )
        for f in cr.files
    ]

    return {
        "id": cr.id,
        "title": cr.title,
        "description": cr.description,
        "status": cr.status,
        "author": cr.author.username,
        "author_id": cr.author_id,
        "reviewer": cr.reviewer.username if cr.reviewer else None,
        "review_comment": cr.review_comment,
        "reviewed_at": cr.reviewed_at.isoformat() if cr.reviewed_at else None,
        "merged_at": cr.merged_at.isoformat() if cr.merged_at else None,
        "created_at": cr.created_at.isoformat(),
        "updated_at": cr.updated_at.isoformat(),
        "files": files,
    }


# ── Create CR ────────────────────────────────────────────────────────────

@router.post("/create")
async def create_change_request(
    req: CreateCRRequest,
    request: Request,
    user: User = Depends(require_role(UserRole.admin.value, UserRole.approver.value, UserRole.editor.value)),
    db: AsyncSession = Depends(get_db),
):
    if not req.title.strip():
        raise HTTPException(400, "Title is required")

    cr = ChangeRequest(
        title=req.title.strip(),
        description=req.description,
        author_id=user.id,
        status=CRStatus.draft.value,
    )
    db.add(cr)
    await db.flush()

    db.add(AuditLog(
        user_id=user.id, action="cr.create", resource_type="change_request",
        resource_id=str(cr.id), ip_address=request.client.host if request.client else "",
    ))

    return {"id": cr.id, "title": cr.title, "status": cr.status}


# ── Update CR ────────────────────────────────────────────────────────────

@router.put("/{cr_id}")
async def update_change_request(
    cr_id: int, req: UpdateCRRequest,
    user: User = Depends(require_role(UserRole.admin.value, UserRole.approver.value, UserRole.editor.value)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    cr = result.scalar_one_or_none()
    if not cr:
        raise HTTPException(404, "Change request not found")
    if cr.author_id != user.id and user.role != UserRole.admin.value:
        raise HTTPException(403, "Only the author or admin can update this CR")
    if cr.status not in (CRStatus.draft.value, CRStatus.rejected.value):
        raise HTTPException(400, "Can only edit draft or rejected CRs")

    if req.title is not None:
        cr.title = req.title.strip()
    if req.description is not None:
        cr.description = req.description

    return {"ok": True}


# ── Add file to CR ──────────────────────────────────────────────────────

@router.post("/{cr_id}/files")
async def add_file_to_cr(
    cr_id: int, req: AddFileRequest,
    user: User = Depends(require_role(UserRole.admin.value, UserRole.approver.value, UserRole.editor.value)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChangeRequest).options(selectinload(ChangeRequest.files))
        .where(ChangeRequest.id == cr_id)
    )
    cr = result.scalar_one_or_none()
    if not cr:
        raise HTTPException(404, "Change request not found")
    if cr.author_id != user.id and user.role != UserRole.admin.value:
        raise HTTPException(403, "Only the author or admin can modify files")
    if cr.status not in (CRStatus.draft.value, CRStatus.rejected.value):
        raise HTTPException(400, "Can only modify files in draft or rejected CRs")

    file_path = req.file_path.strip("/")
    if not file_path:
        raise HTTPException(400, "File path is required")
    if req.action not in (FileAction.create.value, FileAction.edit.value, FileAction.delete.value):
        raise HTTPException(400, "Action must be create, edit, or delete")

    # Remove existing entry for this path if any
    existing = [f for f in cr.files if f.file_path == file_path]
    for e in existing:
        if e.staging_s3_key:
            try:
                await s3.delete_object(e.staging_s3_key)
            except Exception:
                pass
        await db.delete(e)

    staging_key = None
    base_version_id = None

    if req.action in (FileAction.create.value, FileAction.edit.value):
        if req.content is None:
            raise HTTPException(400, "Content is required for create/edit actions")
        content_bytes = req.content.encode("utf-8")
        staging_key = S3Service.generate_staging_key()
        await s3.put_object(staging_key, content_bytes, S3Service.guess_content_type(file_path))

    if req.action in (FileAction.edit.value, FileAction.delete.value):
        # Find the base version
        latest_result = await db.execute(
            select(FileVersion)
            .where(FileVersion.file_path == file_path)
            .order_by(desc(FileVersion.version))
            .limit(1)
        )
        latest = latest_result.scalar_one_or_none()
        if latest and not latest.is_delete:
            base_version_id = latest.id
        elif req.action == FileAction.edit.value:
            raise HTTPException(404, "File does not exist, use 'create' action")

    crf = ChangeRequestFile(
        change_request_id=cr_id,
        file_path=file_path,
        action=req.action,
        staging_s3_key=staging_key,
        base_version_id=base_version_id,
    )
    db.add(crf)
    await db.flush()

    return {"id": crf.id, "file_path": file_path, "action": req.action}


# ── Remove file from CR ─────────────────────────────────────────────────

@router.delete("/{cr_id}/files/{file_id}")
async def remove_file_from_cr(
    cr_id: int, file_id: int,
    user: User = Depends(require_role(UserRole.admin.value, UserRole.approver.value, UserRole.editor.value)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    cr = result.scalar_one_or_none()
    if not cr:
        raise HTTPException(404, "Change request not found")
    if cr.author_id != user.id and user.role != UserRole.admin.value:
        raise HTTPException(403, "Only the author or admin can modify files")

    file_result = await db.execute(
        select(ChangeRequestFile).where(
            ChangeRequestFile.id == file_id,
            ChangeRequestFile.change_request_id == cr_id,
        )
    )
    crf = file_result.scalar_one_or_none()
    if not crf:
        raise HTTPException(404, "File entry not found")

    if crf.staging_s3_key:
        try:
            await s3.delete_object(crf.staging_s3_key)
        except Exception:
            pass
    await db.delete(crf)

    return {"ok": True}


# ── Get diff for a CR file ──────────────────────────────────────────────

@router.get("/{cr_id}/diff/{file_id}")
async def get_cr_file_diff(
    cr_id: int, file_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    file_result = await db.execute(
        select(ChangeRequestFile)
        .options(selectinload(ChangeRequestFile.base_version))
        .where(ChangeRequestFile.id == file_id, ChangeRequestFile.change_request_id == cr_id)
    )
    crf = file_result.scalar_one_or_none()
    if not crf:
        raise HTTPException(404, "File entry not found")

    old_content = ""
    new_content = ""

    if crf.base_version and crf.base_version.s3_key:
        try:
            old_bytes = await s3.get_object(crf.base_version.s3_key)
            old_content = old_bytes.decode("utf-8", errors="replace")
        except Exception:
            old_content = "[Content unavailable]"

    if crf.staging_s3_key:
        try:
            new_bytes = await s3.get_object(crf.staging_s3_key)
            new_content = new_bytes.decode("utf-8", errors="replace")
        except Exception:
            new_content = "[Content unavailable]"

    old_lines = old_content.splitlines()
    new_lines = new_content.splitlines()

    diff = difflib.HtmlDiff(tabsize=4, wrapcolumn=120)
    diff_html = diff.make_table(
        old_lines, new_lines,
        fromdesc=f"Base (v{crf.base_version.version})" if crf.base_version else "Empty",
        todesc="Proposed",
        context=True, numlines=5,
    )

    return {
        "file_path": crf.file_path,
        "action": crf.action,
        "diff_html": diff_html,
        "old_content": old_content,
        "new_content": new_content,
    }


# ── Submit for review ────────────────────────────────────────────────────

@router.post("/{cr_id}/submit")
async def submit_for_review(
    cr_id: int, request: Request,
    user: User = Depends(require_role(UserRole.admin.value, UserRole.approver.value, UserRole.editor.value)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ChangeRequest).options(selectinload(ChangeRequest.files))
        .where(ChangeRequest.id == cr_id)
    )
    cr = result.scalar_one_or_none()
    if not cr:
        raise HTTPException(404, "Change request not found")
    if cr.author_id != user.id and user.role != UserRole.admin.value:
        raise HTTPException(403, "Only the author can submit for review")
    if cr.status not in (CRStatus.draft.value, CRStatus.rejected.value):
        raise HTTPException(400, "Can only submit draft or rejected CRs")
    if not cr.files:
        raise HTTPException(400, "Cannot submit a CR with no files")

    cr.status = CRStatus.pending_review.value
    cr.review_comment = None
    cr.reviewer_id = None
    cr.reviewed_at = None

    db.add(AuditLog(
        user_id=user.id, action="cr.submit", resource_type="change_request",
        resource_id=str(cr.id), ip_address=request.client.host if request.client else "",
    ))

    return {"ok": True, "status": cr.status}


# ── Review (approve/reject) ─────────────────────────────────────────────

@router.post("/{cr_id}/review")
async def review_change_request(
    cr_id: int, req: ReviewRequest, request: Request,
    user: User = Depends(require_role(UserRole.admin.value, UserRole.approver.value)),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone

    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    cr = result.scalar_one_or_none()
    if not cr:
        raise HTTPException(404, "Change request not found")
    if cr.status != CRStatus.pending_review.value:
        raise HTTPException(400, "CR is not pending review")
    if cr.author_id == user.id and user.role != UserRole.admin.value:
        raise HTTPException(400, "Cannot review your own change request")

    if req.action == "approve":
        cr.status = CRStatus.approved.value
    elif req.action == "reject":
        cr.status = CRStatus.rejected.value
    else:
        raise HTTPException(400, "Action must be 'approve' or 'reject'")

    cr.reviewer_id = user.id
    cr.review_comment = req.comment
    cr.reviewed_at = datetime.now(timezone.utc)

    db.add(AuditLog(
        user_id=user.id, action=f"cr.{req.action}", resource_type="change_request",
        resource_id=str(cr.id), ip_address=request.client.host if request.client else "",
    ))

    return {"ok": True, "status": cr.status}


# ── Merge CR ─────────────────────────────────────────────────────────────

@router.post("/{cr_id}/merge")
async def merge_change_request(
    cr_id: int, request: Request,
    user: User = Depends(require_role(UserRole.admin.value, UserRole.approver.value, UserRole.editor.value)),
    db: AsyncSession = Depends(get_db),
):
    from datetime import datetime, timezone

    result = await db.execute(
        select(ChangeRequest)
        .options(selectinload(ChangeRequest.files).selectinload(ChangeRequestFile.base_version))
        .where(ChangeRequest.id == cr_id)
    )
    cr = result.scalar_one_or_none()
    if not cr:
        raise HTTPException(404, "Change request not found")
    if cr.status != CRStatus.approved.value:
        raise HTTPException(400, "CR must be approved before merging")

    # Apply each file change
    for crf in cr.files:
        if crf.action in (FileAction.create.value, FileAction.edit.value):
            if not crf.staging_s3_key:
                continue
            content = await s3.get_object(crf.staging_s3_key)
            content_type = S3Service.guess_content_type(crf.file_path)
            content_hash = S3Service.compute_hash(content)

            # Get next version
            ver_result = await db.execute(
                select(func.max(FileVersion.version)).where(FileVersion.file_path == crf.file_path)
            )
            next_ver = (ver_result.scalar() or 0) + 1
            version_key = S3Service.generate_version_key()

            await s3.put_object(version_key, content, content_type)
            await s3.put_object(crf.file_path, content, content_type)

            fv = FileVersion(
                file_path=crf.file_path, version=next_ver, s3_key=version_key,
                size=len(content), content_hash=content_hash, author_id=cr.author_id,
                message=f"CR #{cr.id}: {cr.title}",
            )
            db.add(fv)

            # Clean up staging
            try:
                await s3.delete_object(crf.staging_s3_key)
            except Exception:
                pass

        elif crf.action == FileAction.delete.value:
            ver_result = await db.execute(
                select(func.max(FileVersion.version)).where(FileVersion.file_path == crf.file_path)
            )
            next_ver = (ver_result.scalar() or 0) + 1

            fv = FileVersion(
                file_path=crf.file_path, version=next_ver, s3_key="",
                size=0, content_hash="deleted", author_id=cr.author_id,
                message=f"CR #{cr.id}: Delete {crf.file_path}", is_delete=True,
            )
            db.add(fv)
            try:
                await s3.delete_object(crf.file_path)
            except Exception:
                pass

    cr.status = CRStatus.merged.value
    cr.merged_at = datetime.now(timezone.utc)

    db.add(AuditLog(
        user_id=user.id, action="cr.merge", resource_type="change_request",
        resource_id=str(cr.id),
        details={"file_count": len(cr.files)},
        ip_address=request.client.host if request.client else "",
    ))

    return {"ok": True, "status": cr.status}


# ── Close CR ─────────────────────────────────────────────────────────────

@router.post("/{cr_id}/close")
async def close_change_request(
    cr_id: int, request: Request,
    user: User = Depends(require_role(UserRole.admin.value, UserRole.approver.value, UserRole.editor.value)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(ChangeRequest).where(ChangeRequest.id == cr_id))
    cr = result.scalar_one_or_none()
    if not cr:
        raise HTTPException(404, "Change request not found")
    if cr.status in (CRStatus.merged.value, CRStatus.closed.value):
        raise HTTPException(400, "CR is already finalized")
    if cr.author_id != user.id and user.role != UserRole.admin.value:
        raise HTTPException(403, "Only the author or admin can close this CR")

    cr.status = CRStatus.closed.value

    # Clean up staging files
    result = await db.execute(
        select(ChangeRequestFile).where(ChangeRequestFile.change_request_id == cr_id)
    )
    for crf in result.scalars().all():
        if crf.staging_s3_key:
            try:
                await s3.delete_object(crf.staging_s3_key)
            except Exception:
                pass

    db.add(AuditLog(
        user_id=user.id, action="cr.close", resource_type="change_request",
        resource_id=str(cr.id), ip_address=request.client.host if request.client else "",
    ))

    return {"ok": True, "status": cr.status}
