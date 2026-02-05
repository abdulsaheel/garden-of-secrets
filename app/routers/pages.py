"""Server-side rendered page routes."""

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_optional_user, get_current_user
from app.config import TEMPLATES_DIR
from app.database import get_db
from app.models import User, FileVersion, ChangeRequest, AuditLog, CRStatus, FileShare
from app.s3 import S3Service

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _redirect_login():
    return RedirectResponse(url="/login", status_code=302)


# ── Auth pages ───────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user: User = Depends(get_optional_user)):
    if user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request, user: User = Depends(get_optional_user)):
    if user:
        return RedirectResponse(url="/", status_code=302)
    return templates.TemplateResponse("register.html", {"request": request})


# ── Dashboard ────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user: User = Depends(get_optional_user), db: AsyncSession = Depends(get_db)):
    if not user:
        return _redirect_login()

    # Recent activity
    recent_logs = await db.execute(
        select(AuditLog).options(selectinload(AuditLog.user))
        .order_by(desc(AuditLog.created_at)).limit(20)
    )
    logs = recent_logs.scalars().all()

    # Pending CRs
    pending_crs = await db.execute(
        select(ChangeRequest).options(selectinload(ChangeRequest.author))
        .where(ChangeRequest.status == CRStatus.pending_review.value)
        .order_by(desc(ChangeRequest.created_at)).limit(10)
    )
    pending = pending_crs.scalars().all()

    # Recent files
    subq = (
        select(FileVersion.file_path, func.max(FileVersion.version).label("max_ver"))
        .group_by(FileVersion.file_path).subquery()
    )
    recent_files = await db.execute(
        select(FileVersion).options(selectinload(FileVersion.author))
        .join(subq, and_(
            FileVersion.file_path == subq.c.file_path,
            FileVersion.version == subq.c.max_ver,
        ))
        .where(FileVersion.is_delete == False)
        .order_by(desc(FileVersion.created_at)).limit(10)
    )
    files = recent_files.scalars().all()

    # Stats
    total_files = (await db.execute(
        select(func.count(func.distinct(FileVersion.file_path))).where(FileVersion.is_delete == False)
    )).scalar() or 0
    total_versions = (await db.execute(select(func.count(FileVersion.id)))).scalar() or 0
    total_crs = (await db.execute(select(func.count(ChangeRequest.id)))).scalar() or 0

    return templates.TemplateResponse("dashboard.html", {
        "request": request, "user": user,
        "logs": logs, "pending_crs": pending, "recent_files": files,
        "total_files": total_files, "total_versions": total_versions, "total_crs": total_crs,
    })


# ── File browser ─────────────────────────────────────────────────────────

@router.get("/browse", response_class=HTMLResponse)
@router.get("/browse/{path:path}", response_class=HTMLResponse)
async def browser_page(request: Request, path: str = "", user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    return templates.TemplateResponse("browser.html", {
        "request": request, "user": user, "current_path": path,
    })


# ── File editor ──────────────────────────────────────────────────────────

@router.get("/edit", response_class=HTMLResponse)
async def editor_page(request: Request, path: str = "", user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    return templates.TemplateResponse("editor.html", {
        "request": request, "user": user, "file_path": path, "mode": "edit",
    })


@router.get("/new", response_class=HTMLResponse)
async def new_file_page(request: Request, path: str = "", user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    return templates.TemplateResponse("editor.html", {
        "request": request, "user": user, "file_path": path, "mode": "new",
    })


# ── Diff viewer ──────────────────────────────────────────────────────────

@router.get("/diff", response_class=HTMLResponse)
async def diff_page(
    request: Request, path: str = "", old: int = 0, new: int = 0,
    user: User = Depends(get_optional_user),
):
    if not user:
        return _redirect_login()
    return templates.TemplateResponse("diff.html", {
        "request": request, "user": user,
        "file_path": path, "old_version": old, "new_version": new,
    })


# ── Version history ──────────────────────────────────────────────────────

@router.get("/history", response_class=HTMLResponse)
async def history_page(request: Request, path: str = "", user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    return templates.TemplateResponse("history.html", {
        "request": request, "user": user, "file_path": path,
    })


# ── Change requests ──────────────────────────────────────────────────────

@router.get("/change-requests", response_class=HTMLResponse)
async def change_requests_page(request: Request, user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    return templates.TemplateResponse("change_requests.html", {
        "request": request, "user": user,
    })


@router.get("/change-requests/new", response_class=HTMLResponse)
async def new_cr_page(request: Request, user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    return templates.TemplateResponse("cr_new.html", {
        "request": request, "user": user,
    })


@router.get("/change-requests/{cr_id}", response_class=HTMLResponse)
async def cr_detail_page(request: Request, cr_id: int, user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    return templates.TemplateResponse("cr_detail.html", {
        "request": request, "user": user, "cr_id": cr_id,
    })


# ── Admin pages ──────────────────────────────────────────────────────────

@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users_page(request: Request, user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return templates.TemplateResponse("admin_users.html", {
        "request": request, "user": user,
    })


@router.get("/admin/audit-log", response_class=HTMLResponse)
async def admin_audit_page(request: Request, user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return templates.TemplateResponse("admin_audit.html", {
        "request": request, "user": user,
    })


# ── Public file access (no auth) ─────────────────────────────────────

s3 = S3Service()

@router.get("/public/{token}", response_class=HTMLResponse)
async def public_file(request: Request, token: str, db: AsyncSession = Depends(get_db)):
    """Serve a publicly shared file — no authentication required."""
    result = await db.execute(
        select(FileShare).where(FileShare.token == token)
    )
    share = result.scalar_one_or_none()
    if not share or not share.is_public:
        raise HTTPException(404, "File not found or not public")

    if share.is_archived:
        return templates.TemplateResponse("public_file.html", {
            "request": request,
            "error": "This file has been archived and is no longer available.",
            "file_path": share.file_path,
        })

    # Get latest non-deleted version
    ver_result = await db.execute(
        select(FileVersion)
        .where(FileVersion.file_path == share.file_path)
        .order_by(desc(FileVersion.version))
        .limit(1)
    )
    fv = ver_result.scalar_one_or_none()
    if not fv or fv.is_delete:
        raise HTTPException(404, "File not found or deleted")

    try:
        content_bytes = await s3.get_object(share.file_path)
    except Exception:
        raise HTTPException(404, "File content not found")

    is_text = S3Service.is_text_file(share.file_path)
    if is_text:
        content = content_bytes.decode("utf-8", errors="replace")
        return templates.TemplateResponse("public_file.html", {
            "request": request,
            "file_path": share.file_path,
            "content": content,
            "size": len(content_bytes),
            "error": None,
        })
    else:
        content_type = S3Service.guess_content_type(share.file_path)
        filename = share.file_path.rsplit("/", 1)[-1] if "/" in share.file_path else share.file_path
        return Response(
            content=content_bytes,
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@router.get("/public/raw/{path:path}")
async def public_file_raw(path: str, db: AsyncSession = Depends(get_db)):
    """Return the raw file bytes for a public share (no preview/template). Path is the file path (e.g. test/test.json)."""
    result = await db.execute(
        select(FileShare).where(FileShare.file_path == path)
    )
    share = result.scalar_one_or_none()
    if not share or not share.is_public:
        raise HTTPException(404, "File not found or not public")

    if share.is_archived:
        raise HTTPException(410, "File has been archived")

    # Get latest non-deleted version
    ver_result = await db.execute(
        select(FileVersion)
        .where(FileVersion.file_path == share.file_path)
        .order_by(desc(FileVersion.version))
        .limit(1)
    )
    fv = ver_result.scalar_one_or_none()
    if not fv or fv.is_delete:
        raise HTTPException(404, "File not found or deleted")

    try:
        content_bytes = await s3.get_object(share.file_path)
    except Exception:
        raise HTTPException(404, "File content not found")

    content_type = S3Service.guess_content_type(share.file_path)
    filename = share.file_path.rsplit("/", 1)[-1] if "/" in share.file_path else share.file_path
    return Response(
        content=content_bytes,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Settings ─────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, user: User = Depends(get_optional_user)):
    if not user:
        return _redirect_login()
    return templates.TemplateResponse("settings.html", {
        "request": request, "user": user,
    })
