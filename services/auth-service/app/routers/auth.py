from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, Header, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.keys import key_material
from app.models import RefreshToken, User
from app.redis_client import blacklist_add, is_blacklisted
from app.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
)
from app.security import create_access_token, hash_password, verify_password
from gestalt_shared.errors import AppError

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(db: Session, user: User) -> TokenResponse:
    access_token = create_access_token(user.id, user.email)
    refresh = RefreshToken(
        user_id=user.id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=settings.jwt_refresh_token_ttl_seconds),
    )
    db.add(refresh)
    db.commit()
    return TokenResponse(
        accessToken=access_token,
        refreshToken=refresh.token_id,
        expiresIn=settings.jwt_access_token_ttl_seconds,
    )


def _current_user_id(authorization: str = Header(default="")) -> str:
    if not authorization.lower().startswith("bearer "):
        raise AppError("UNAUTHENTICATED", "Missing bearer token", 401)
    token = authorization.split(" ", 1)[1]
    try:
        claims = jwt.decode(
            token, key=key_material.public_key, algorithms=["RS256"], issuer=settings.jwt_issuer
        )
    except jwt.PyJWTError as exc:
        raise AppError("INVALID_TOKEN", f"Token verification failed: {exc}", 401) from exc
    return claims["sub"]


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    existing = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if existing:
        raise AppError("EMAIL_TAKEN", "An account with this email already exists", 409)
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    return RegisterResponse(id=user.id, email=user.email)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.email == body.email)).scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "Email or password is incorrect", 401)
    return _issue_tokens(db, user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    token_id = body.refreshToken

    # Blacklist check must happen before the DB lookup -- otherwise there is a
    # window where a just-revoked token still validates (auth-service.md).
    if is_blacklisted(token_id):
        raise AppError("INVALID_TOKEN", "Refresh token has been revoked", 401)

    row = db.get(RefreshToken, token_id)
    now = datetime.now(timezone.utc)
    if row is None or row.revoked or row.expires_at.replace(tzinfo=timezone.utc) < now:
        raise AppError("INVALID_TOKEN", "Refresh token is invalid or expired", 401)

    user = db.get(User, row.user_id)
    if user is None:
        raise AppError("INVALID_TOKEN", "Refresh token is invalid or expired", 401)

    # Rotate: revoke the presented token, issue a brand new one.
    row.revoked = True
    remaining_ttl = int((row.expires_at.replace(tzinfo=timezone.utc) - now).total_seconds())
    blacklist_add(token_id, remaining_ttl)
    db.commit()

    return _issue_tokens(db, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: LogoutRequest, db: Session = Depends(get_db), user_id: str = Depends(_current_user_id)):
    row = db.get(RefreshToken, body.refreshToken)
    if row is not None and row.user_id == user_id and not row.revoked:
        now = datetime.now(timezone.utc)
        row.revoked = True
        remaining_ttl = int((row.expires_at.replace(tzinfo=timezone.utc) - now).total_seconds())
        blacklist_add(body.refreshToken, remaining_ttl)
        db.commit()
    return None


@router.get("/.well-known/jwks.json")
def jwks():
    return key_material.jwks()
