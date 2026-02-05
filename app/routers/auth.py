"""Authentication API routes."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, verify_password, create_access_token, get_current_user
from app.database import get_db
from app.models import User, UserRole, AuditLog
from app.config import get_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str
    full_name: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: str
    role: str
    is_active: bool

    model_config = {"from_attributes": True}


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest, request: Request, db: AsyncSession = Depends(get_db)):
    if len(req.username) < 3 or len(req.username) > 150:
        raise HTTPException(400, "Username must be 3-150 characters")
    if len(req.password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    exists = await db.execute(
        select(User).where((User.username == req.username) | (User.email == req.email))
    )
    if exists.scalar_one_or_none():
        raise HTTPException(409, "Username or email already taken")

    # First user gets admin role
    count = await db.execute(select(func.count(User.id)))
    total_users = count.scalar()
    role = UserRole.admin.value if total_users == 0 and settings.auto_admin_first_user else UserRole.viewer.value

    user = User(
        username=req.username,
        email=req.email,
        password_hash=hash_password(req.password),
        full_name=req.full_name,
        role=role,
    )
    db.add(user)
    await db.flush()

    db.add(AuditLog(
        user_id=user.id, action="user.register", resource_type="user",
        resource_id=str(user.id), ip_address=request.client.host if request.client else "",
    ))
    return user


@router.post("/login")
async def login(req: LoginRequest, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == req.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    if not user.is_active:
        raise HTTPException(403, "Account is deactivated")

    token = create_access_token(user.id, user.username)
    response.set_cookie(
        key="access_token", value=token, httponly=True,
        max_age=settings.access_token_expire_minutes * 60,
        samesite="lax",
    )

    db.add(AuditLog(
        user_id=user.id, action="user.login", resource_type="user",
        resource_id=str(user.id), ip_address=request.client.host if request.client else "",
    ))

    return {"access_token": token, "token_type": "bearer", "user": UserResponse.model_validate(user)}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user
