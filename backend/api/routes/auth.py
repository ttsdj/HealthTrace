from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.models import User
from backend.infra.auth import (
    authenticate_user,
    create_access_token,
    get_current_user,
    get_db,
    get_password_hash,
    resolve_role,
)
from backend.schemas import AuthResponse, CurrentUserResponse, LoginRequest, RegisterRequest
from backend.patient.scope import ensure_user_scope

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=AuthResponse)
async def register(request: RegisterRequest, db: Session = Depends(get_db)):
    username = (request.username or "").strip()
    password = (request.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名和密码不能为空")

    exists = db.query(User).filter(User.username == username).first()
    if exists:
        raise HTTPException(status_code=409, detail="用户名已存在")

    role = resolve_role(request.role, request.admin_code)
    user = User(username=username, password_hash=get_password_hash(password), role=role)
    db.add(user)
    db.flush()
    scope = ensure_user_scope(db, user)
    db.commit()

    token = create_access_token(username=username, role=role)
    return AuthResponse(
        access_token=token,
        username=username,
        role=role,
        tenant_id=scope.tenant_id,
        patient_id=scope.patient_id,
    )


@router.post("/auth/login", response_model=AuthResponse)
async def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, request.username, request.password)
    if not user:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    scope = ensure_user_scope(db, user)
    db.commit()
    token = create_access_token(username=user.username, role=user.role)
    return AuthResponse(
        access_token=token,
        username=user.username,
        role=user.role,
        tenant_id=scope.tenant_id,
        patient_id=scope.patient_id,
    )


@router.get("/auth/me", response_model=CurrentUserResponse)
async def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    scope = ensure_user_scope(db, current_user)
    db.commit()
    return CurrentUserResponse(
        username=current_user.username,
        role=current_user.role,
        tenant_id=scope.tenant_id,
        patient_id=scope.patient_id,
    )
