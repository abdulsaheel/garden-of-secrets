"""Admin API routes: user management, audit logs, settings."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user, require_role, hash_password
from app.database import get_db
from app.models import User, UserRole, AuditLog

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ── Schemas ──────────────────────────────────────────────────────────────

class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    full_name: Optional[str] = None
    email: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    new_password: str


class AuditLogEntry(BaseModel):
    id: int
    user: Optional[str] = None
    action: str
    resource_type: str
    resource_id: str
    details: dict
    ip_address: str
    created_at: str


# ── Users ────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: User = Depends(require_role(UserRole.admin.value)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).order_by(User.created_at)
        .offset((page - 1) * per_page).limit(per_page)
    )
    users = result.scalars().all()
    total = (await db.execute(select(func.count(User.id)))).scalar()

    return {
        "items": [
            {
                "id": u.id, "username": u.username, "email": u.email,
                "full_name": u.full_name, "role": u.role,
                "is_active": u.is_active,
                "created_at": u.created_at.isoformat(),
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.put("/users/{user_id}")
async def update_user(
    user_id: int, req: UpdateUserRequest, request: Request,
    admin: User = Depends(require_role(UserRole.admin.value)),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    if req.role is not None:
        if req.role not in (UserRole.admin.value, UserRole.approver.value, UserRole.editor.value, UserRole.viewer.value):
            raise HTTPException(400, "Invalid role")
        if target.id == admin.id and req.role != UserRole.admin.value:
            raise HTTPException(400, "Cannot demote yourself")
        target.role = req.role

    if req.is_active is not None:
        if target.id == admin.id and not req.is_active:
            raise HTTPException(400, "Cannot deactivate yourself")
        target.is_active = req.is_active

    if req.full_name is not None:
        target.full_name = req.full_name
    if req.email is not None:
        target.email = req.email

    db.add(AuditLog(
        user_id=admin.id, action="admin.update_user", resource_type="user",
        resource_id=str(user_id), details=req.model_dump(exclude_none=True),
        ip_address=request.client.host if request.client else "",
    ))

    return {"ok": True}


@router.post("/users/{user_id}/reset-password")
async def reset_user_password(
    user_id: int, req: ResetPasswordRequest, request: Request,
    admin: User = Depends(require_role(UserRole.admin.value)),
    db: AsyncSession = Depends(get_db),
):
    if len(req.new_password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")

    target.password_hash = hash_password(req.new_password)

    db.add(AuditLog(
        user_id=admin.id, action="admin.reset_password", resource_type="user",
        resource_id=str(user_id),
        ip_address=request.client.host if request.client else "",
    ))

    return {"ok": True}


# ── Audit Logs ───────────────────────────────────────────────────────────

@router.get("/audit-logs")
async def list_audit_logs(
    action: Optional[str] = None,
    resource_type: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=100),
    user: User = Depends(require_role(UserRole.admin.value)),
    db: AsyncSession = Depends(get_db),
):
    query = select(AuditLog).options(selectinload(AuditLog.user)).order_by(desc(AuditLog.created_at))
    if action:
        query = query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    query = query.offset((page - 1) * per_page).limit(per_page)

    result = await db.execute(query)
    logs = result.scalars().all()

    count_q = select(func.count(AuditLog.id))
    if action:
        count_q = count_q.where(AuditLog.action == action)
    if resource_type:
        count_q = count_q.where(AuditLog.resource_type == resource_type)
    total = (await db.execute(count_q)).scalar()

    return {
        "items": [
            AuditLogEntry(
                id=l.id, user=l.user.username if l.user else None,
                action=l.action, resource_type=l.resource_type,
                resource_id=l.resource_id, details=l.details or {},
                ip_address=l.ip_address,
                created_at=l.created_at.isoformat(),
            )
            for l in logs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ── Stats ────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(
    user: User = Depends(require_role(UserRole.admin.value)),
    db: AsyncSession = Depends(get_db),
):
    from app.models import FileVersion, ChangeRequest

    users_total = (await db.execute(select(func.count(User.id)))).scalar()
    users_active = (await db.execute(select(func.count(User.id)).where(User.is_active == True))).scalar()
    files_total = (await db.execute(
        select(func.count(func.distinct(FileVersion.file_path))).where(FileVersion.is_delete == False)
    )).scalar()
    versions_total = (await db.execute(select(func.count(FileVersion.id)))).scalar()
    cr_total = (await db.execute(select(func.count(ChangeRequest.id)))).scalar()
    cr_pending = (await db.execute(
        select(func.count(ChangeRequest.id)).where(ChangeRequest.status == "pending_review")
    )).scalar()

    return {
        "users_total": users_total,
        "users_active": users_active,
        "files_total": files_total,
        "versions_total": versions_total,
        "cr_total": cr_total,
        "cr_pending": cr_pending,
    }
