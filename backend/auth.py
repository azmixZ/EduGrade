# backend/auth.py
import os
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from sqlalchemy.orm import Session

import models
from database import get_db
from security import load_secret_key

SECRET_KEY = load_secret_key()          # из KKCI_SECRET_KEY или автоматически созданного файла .secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("KKCI_TOKEN_MINUTES", "720"))   # 12 часов

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")

# Хеш-заглушка: проверяется, когда логина нет в базе, чтобы время ответа не выдавало существование аккаунта
DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        raw = plain_password.encode("utf-8")
        if len(raw) > 72:               # bcrypt принимает максимум 72 байта
            return False
        return bcrypt.checkpw(raw, hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def get_password_hash(password: str) -> str:
    raw = password.encode("utf-8")
    if len(raw) > 72:
        raise ValueError("Пароль длиннее 72 байт")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def create_access_token(user: models.User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.username,
        "ver": user.token_version or 0,     # смена пароля / деактивация делают старые токены недействительными
        "iat": now,
        "exp": now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _credentials_exception():
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Не удалось проверить учетные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_user(token: str, db: Session) -> models.User:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"require_exp": True})
        username = payload.get("sub")
        version = payload.get("ver")
    except JWTError:
        raise _credentials_exception()
    if not username or version is None:
        raise _credentials_exception()

    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None or not user.is_active or (user.token_version or 0) != version:
        raise _credentials_exception()
    return user


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> models.User:
    return _resolve_user(token, db)


def check_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Недостаточно прав. Требуется роль Администратора.")
    return current_user
